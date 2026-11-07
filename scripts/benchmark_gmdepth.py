"""Offline posed two-view depth probe; separate COLMAP, Torch and scoring processes."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
from PIL import Image
from scripts.benchmark_depth_prior import digest, fit_cells, sample_depth, save
from scripts.benchmark_sensor_depth import score_depth

UPSTREAM = '943a99006950ab6c84ea72a72beb147fdd03c19a'
WEIGHTS_SHA256 = 'd3d1efb5d1615404828574b6bad63e138c8f4fd8077557392b18b57324982b49'


def network_intrinsics(k, original_size, target_size):
    """COLMAP corner coordinates -> resized zero-index pixel centers (Pillow)."""
    k = np.asarray(k, float).copy()
    scale = np.asarray(target_size, float) / original_size
    k[:2] *= scale[:, None]
    k[:2, 2] -= .5
    return k


def relative_pose(source, target):
    return np.asarray(target) @ np.linalg.inv(source)


def choose_target(source, views, tracks, points):
    candidates = []
    center = lambda v: -np.asarray(v['pose'])[:3, :3].T @ np.asarray(v['pose'])[:3, 3]
    for target in views:
        if source['name'] == target['name']:
            continue
        shared = sorted(tracks[source['name']] & tracks[target['name']])
        if len(shared) < 20:
            continue
        xyz = np.array([points[i] for i in shared])
        a, b = xyz-center(source), xyz-center(target)
        angles = np.degrees(np.arccos(np.clip(np.sum(a*b, axis=1)/np.linalg.norm(a, axis=1)/np.linalg.norm(b, axis=1), -1, 1)))
        angle = float(np.median(angles))
        if 1 <= angle <= 30:
            candidates.append((-len(shared), target['name'], angle, target))
    if not candidates:
        raise ValueError('No training target with 20 common tracks and 1–30 degree median parallax')
    count, _, angle, target = min(candidates, key=lambda x: x[:2])
    return target, -count, angle


def prepare(args):
    import pycolmap
    args.output.mkdir(parents=True, exist_ok=False)
    result = json.loads((args.work/'result.json').read_text())
    assert result['status'] == 'complete' and result['report']['evaluation_verified']
    held = {Path(v['name']).stem for v in result['report']['evaluation_images']}
    model = pycolmap.Reconstruction(args.work/'sfm/undistorted/sparse')
    views, tracks = [], {}
    points = {i: p.xyz for i, p in model.points3D.items()}
    for im in sorted(model.images.values(), key=lambda v: v.name):
        if Path(im.name).stem in held:
            continue
        camera = model.cameras[im.camera_id]
        assert camera.model.name == 'PINHOLE'
        pose = np.eye(4); pose[:3] = im.cam_from_world().matrix()
        image = args.work/'sfm/undistorted/images'/im.name
        obs = [o for o in im.points2D if o.has_point3D()]
        xy = np.array([o.xy for o in obs]); xyz = np.array([points[o.point3D_id] for o in obs])
        z = (xyz @ pose[:3, :3].T + pose[:3, 3])[:, 2]
        good = np.isfinite(z) & (z > 0)
        views.append(dict(name=im.name, image=str(image.resolve()), sha256=digest(image),
                          size=[camera.width, camera.height], K=camera.calibration_matrix().tolist(),
                          pose=pose.tolist(), xy=xy[good].tolist(), z=z[good].tolist()))
        tracks[im.name] = {o.point3D_id for o in obs}
    if args.references:
        stems = {Path(v['name']).stem for v in json.loads(args.references.read_text())['views']}
        refs = [v for v in views if Path(v['name']).stem in stems]
        assert len(refs) == len(stems)
    else:
        refs = views[:1]
    records = []
    for source in refs:
        target, shared, angle = choose_target(source, views, tracks, points)
        assert source['size'] == target['size'] and np.allclose(source['K'], target['K'])
        fit = fit_cells(source['xy'], *source['size'])
        values = np.asarray(source['z'])[fit]
        assert len(values) >= 20
        near, far = np.quantile(values, [.01, .99]) * [.5, 2]
        size = [int(np.ceil(n/max(source['size'])*512/16)*16) for n in source['size']]
        records.append(dict(source=source, target=target, shared_tracks=shared, median_angle=angle,
                            near=float(near), far=float(far), size=size,
                            K=network_intrinsics(source['K'], source['size'], size).tolist(),
                            pose=relative_pose(source['pose'], target['pose']).tolist()))
    save(args.output/'pairs.json', dict(status='complete', work=str(args.work.resolve()),
        result_sha256=digest(args.work/'result.json'), model_sha256=digest(args.work/'artifacts/gaussians.ply'),
        sparse_sha256={p.name:digest(p) for p in (args.work/'sfm/undistorted/sparse').glob('*.bin')},
        selection='first training source or pre-existing probes; most common tracks; 1–30 degree median parallax',
        depth_bounds='fit-region SfM Z: q01*0.5 to q99*2; no sensor scale or depth', records=records))
    print(args.work.name, len(records), 'pairs prepared', flush=True)


def infer(args):
    import torch
    args.output.mkdir(parents=True, exist_ok=False)
    out = dict(status='running', pairs_sha256=digest(args.pairs), device=args.device, torch=torch.__version__, views=[])
    started = time.monotonic()
    try:
        assert subprocess.check_output(['git','-C',str(args.repo),'rev-parse','HEAD'], text=True).strip() == UPSTREAM
        assert not subprocess.check_output(['git','-C',str(args.repo),'status','--porcelain'],text=True).strip()
        out.update(upstream=UPSTREAM, weights_sha256=digest(args.weights), weights_bytes=args.weights.stat().st_size)
        if out['weights_sha256'] != WEIGHTS_SHA256:
            raise ValueError('Expected the frozen GMDepth ScanNet weights')
        sys.path.insert(0, str(args.repo.resolve()))
        from unimatch.unimatch import UniMatch
        torch.set_num_threads(4)
        model = UniMatch(task='depth').eval()
        model.load_state_dict(torch.load(args.weights, map_location='cpu', weights_only=True)['model'], strict=True)
        model.to(args.device)
        mean = torch.tensor([.485,.456,.406])[None,:,None,None].to(args.device)
        std = torch.tensor([.229,.224,.225])[None,:,None,None].to(args.device)
        records = json.loads(args.pairs.read_text())['records']
        if args.limit: records = records[:args.limit]
        for row in records:
            tensors=[]
            for v in [row['source'],row['target']]:
                assert digest(v['image']) == v['sha256']
                with Image.open(v['image']) as im:
                    assert list(im.size) == v['size']
                    array=np.array(im.convert('RGB').resize(row['size'],Image.Resampling.BILINEAR))
                tensors.append((torch.from_numpy(array).permute(2,0,1)[None].float().to(args.device)/255-mean)/std)
            k=torch.tensor(row['K'],dtype=torch.float32,device=args.device)[None]
            pose=torch.tensor(row['pose'],dtype=torch.float32,device=args.device)[None]
            if args.device=='mps': torch.mps.synchronize()
            begin=time.monotonic()
            with torch.inference_mode():
                depth=model(*tensors,task='depth',intrinsics=k,pose=pose,min_depth=1/row['far'],max_depth=1/row['near'],
                            attn_type='swin',attn_splits_list=[2],prop_radius_list=[-1],num_depth_candidates=64)['flow_preds'][-1]
            if args.device=='mps': torch.mps.synchronize()
            elapsed=time.monotonic()-begin
            array=depth[0].cpu().numpy()
            assert array.shape == tuple(row['size'][::-1]) and np.isfinite(array).all() and (array>0).all()
            path=args.output/(Path(row['source']['name']).stem+'.npy'); np.save(path,array)
            out['views'].append(dict(name=row['source']['name'],depth=path.name,sha256=digest(path),seconds=elapsed,
                                    min=float(array.min()),max=float(array.max()),size=row['size']))
            print(row['source']['name'], elapsed, flush=True)
        out['status']='complete'
    except BaseException as error:
        out.update(status='failed',error=f'{type(error).__name__}: {error}')
        raise
    finally:
        out['seconds']=time.monotonic()-started
        save(args.output/'inference.json',out)


def score(args):
    args.output.mkdir(parents=True, exist_ok=False)
    pairs=json.loads(args.pairs.read_text()); inference=json.loads((args.predictions/'inference.json').read_text())
    assert inference['status']=='complete' and inference['pairs_sha256']==digest(args.pairs)
    rows={r['source']['name']:r for r in pairs['records']}
    work=Path(pairs['work'])
    assert digest(work/'result.json')==pairs['result_sha256']
    assert {p.name:digest(p) for p in (work/'sfm/undistorted/sparse').glob('*.bin')}==pairs['sparse_sha256']
    output=dict(status='complete',pairs_sha256=digest(args.pairs),inference=inference,views=[])
    for prediction in inference['views']:
        row=rows[prediction['name']]; v=row['source']
        path=args.predictions/prediction['depth']; assert digest(path)==prediction['sha256']
        depth=np.load(path,allow_pickle=False)
        xy=np.asarray(v['xy']); z=np.asarray(v['z']); check=~fit_cells(xy,*v['size'])
        xy_net=xy*np.asarray(row['size'])/v['size']
        samples,_=sample_depth(depth,xy_net)
        sparse_score=score_depth(samples[check],z[check])
        metrics=sparse_score['valid_prediction_metrics']
        if metrics: metrics['rmse_sfm_units']=metrics.pop('rmse_m')
        output['views'].append(dict(name=v['name'],target=row['target']['name'],shared_tracks=row['shared_tracks'],
            median_angle=row['median_angle'],near=row['near'],far=row['far'],
            sparse_check=sparse_score))
    if args.sensor_root:
        # Use the frozen raw-camera audit grid for both methods, after inference.
        import pycolmap
        from scripts.benchmark_depth_consistency import grid
        root=args.sensor_root
        evaluation=json.loads((root/'evaluation/sensor-evaluation.json').read_text())
        manifest=json.loads((root/'sensor/sensor.json').read_text())
        assert evaluation['status']=='complete'
        assert evaluation['manifest_sha256']==digest(root/'sensor/sensor.json')
        assert manifest['anchor_sha256']==digest(root/'sensor/anchors.json')
        anchors={Path(v['name']).stem:v for v in json.loads((root/'sensor/anchors.json').read_text())['views']}
        prior={Path(v['name']).stem:v for v in evaluation['views']}
        work=Path(pairs['work'])
        assert work== (root/'jobs/tum-baseline').resolve()
        assert digest(work/'result.json')==pairs['result_sha256']
        output['sensor_evaluation_sha256']=digest(root/'evaluation/sensor-evaluation.json')
        for row,prediction in zip(output['views'],inference['views'],strict=True):
            key=Path(row['name']).stem; view=anchors[key]; pair=rows[row['name']]
            camera=pycolmap.Camera(model=view['camera_model'],width=view['width'],height=view['height'],params=view['camera_params'])
            xy,shape=grid(view['width'],view['height'],evaluation['config']['grid_step'])
            rays=camera.cam_from_img(xy)
            uv=np.c_[rays,np.ones(len(rays))] @ np.asarray(pair['source']['K']).T
            uv=uv[:,:2]*np.asarray(pair['size'])/pair['source']['size']
            depth=np.load(args.predictions/prediction['depth'],allow_pickle=False)
            samples,inside=sample_depth(depth,uv)
            record=prior[key]; path=root/'evaluation'/record['map']
            assert digest(path)==record['map_sha256']
            with np.load(path,allow_pickle=False) as values:
                check=values['check'].ravel(); reference=values['sensor_m'].ravel(); mono=values['prior_m'].ravel()
            assert len(check)==len(samples)
            keep=check & inside
            row['sensor']=dict(check_grid=int(check.sum()),outside_network=int((check & ~inside).sum()),
                gmdepth=score_depth(samples[keep]*evaluation['scale_m_per_sfm_unit'],reference[keep]),
                monocular=score_depth(mono[keep],reference[keep]))
            np.savez_compressed(args.output/(key+'.npz'),gmdepth=samples.reshape(shape)*evaluation['scale_m_per_sfm_unit'],
                                prior=mono.reshape(shape),sensor=reference.reshape(shape),check=keep.reshape(shape))
    save(args.output/'scores.json',output)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='stage',required=True)
    a=sub.add_parser('prepare'); a.add_argument('--work',type=Path,required=True); a.add_argument('--references',type=Path)
    a=sub.add_parser('infer'); a.add_argument('--pairs',type=Path,required=True); a.add_argument('--repo',type=Path,required=True)
    a.add_argument('--weights',type=Path,required=True); a.add_argument('--device',choices=['cpu','mps'],required=True); a.add_argument('--limit',type=int)
    a=sub.add_parser('score'); a.add_argument('--pairs',type=Path,required=True); a.add_argument('--predictions',type=Path,required=True)
    a.add_argument('--sensor-root',type=Path)
    for a in sub.choices.values(): a.add_argument('--output',type=Path,required=True)
    args=p.parse_args(); globals()[args.stage](args)
