"""Audit colors at reciprocal sparse training observations, without correcting them.

Sparse tracks are correspondences, not verified diffuse surfaces or exposure meters.
Run this in a separate process from Torch on macOS (distinct OpenMP runtimes).
"""
import argparse
from collections import Counter, defaultdict
import hashlib
from itertools import combinations
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image


def sample_rgb(pixels, xy):
    """COLMAP centers (0.5, 0.5) become array coordinates (0, 0)."""
    x, y = np.asarray(xy, dtype=float) - 0.5
    h, w = pixels.shape[:2]
    if not np.isfinite([x, y]).all() or not (0 <= x < w - 1 and 0 <= y < h - 1):
        return None
    ix, iy = int(x), int(y)
    fx, fy = x-ix, y-iy
    patch = pixels[iy:iy+2, ix:ix+2].astype(np.float64) / 255
    return (1-fy)*((1-fx)*patch[0, 0]+fx*patch[0, 1])+fy*((1-fx)*patch[1, 0]+fx*patch[1, 1])


def training_pairs(observations, training_ids):
    """One vote per track and distinct training camera pair; never sample holdout."""
    seen = set()
    selected = []
    for image_id, rgb in observations:
        if image_id not in training_ids:
            continue
        if image_id in seen:
            raise ValueError('Duplicate camera in track')
        seen.add(image_id)
        selected.append((image_id, np.asarray(rgb)))
    for (a, ca), (b, cb) in combinations(sorted(selected, key=lambda item: item[0]), 2):
        yield (a, b), cb-ca


def summarize_pairs(pairs, names, minimum=20):
    rows = []
    for (a, b), deltas in sorted(pairs.items()):
        d = np.asarray(deltas, dtype=float)
        absolute = np.abs(d).mean(axis=1)
        rows.append(dict(images=[names[a], names[b]], tracks=len(d), eligible=len(d)>=minimum,
                         median_rgb_delta=np.median(d, axis=0).tolist(),
                         median_abs_rgb=float(np.median(absolute)),
                         p90_abs_rgb=float(np.quantile(absolute, 0.9))))
    eligible = [r for r in rows if r['eligible']]
    return dict(pairs=rows, eligible_pairs=len(eligible), minimum_tracks=minimum,
                median_of_pair_median_abs_rgb=float(np.median([r['median_abs_rgb'] for r in eligible])) if eligible else None)


def unique_camera_elements(elements):
    """Exclude every observation from an ambiguous image, retain other images."""
    counts = Counter(e.image_id for e in elements)
    return sorted((e for e in elements if counts[e.image_id] == 1),
                  key=lambda e: e.image_id), {
        iid for iid, count in counts.items() if count > 1
    }


def measure(data, job, focus_index=8, duplicate_policy='exclude-image'):
    import pycolmap

    if duplicate_policy not in ('exclude-image', 'reject-track'):
        raise ValueError('Unknown duplicate policy')
    start = time.perf_counter()
    work = data/'jobs'/job
    report = json.loads((work/'artifacts/report.json').read_text())
    if report.get('evaluation_mode') != 'holdout' or not report.get('evaluation_verified'):
        raise ValueError('Requires verified held-out training split')
    scene = json.loads((work/'sfm/scene.json').read_text())
    folder = work/'sfm/undistorted'
    model = pycolmap.Reconstruction(folder/'sparse')
    images = sorted(model.images.items(), key=lambda pair: pair[1].name)
    views = sorted(scene['views'], key=lambda view: view['image_name'])
    if [im.name for _,im in images] != [v['image_name'] for v in views]:
        raise ValueError('Sparse image set differs from scene')
    if not 0 <= focus_index < len(images) or focus_index % 8:
        raise ValueError('Focus must be a held-out index')
    training = {iid for i,(iid,_) in enumerate(images) if i%8}
    if len(training)!=report['training_views'] or len(images)-len(training)!=report['evaluation_views']:
        raise ValueError('Training split mismatch')
    names = {iid:im.name for iid,im in images}
    pixels, identity = {}, {}
    for (iid, im), view in zip(images,views):
        camera = model.cameras[im.camera_id]
        pose = im.cam_from_world().matrix().astype(np.float32)
        center = np.asarray(scene['normalization']['center'], dtype=np.float32)
        pose[:,3] = (pose[:,:3]@center+pose[:,3])/scene['normalization']['scale']
        if (camera.model.name!='PINHOLE' or (camera.width,camera.height)!=(view['width'],view['height'])
            or not np.allclose(pose,np.asarray(view['view'])[:3],rtol=0,atol=2e-6)
            or not np.allclose(camera.calibration_matrix(),view['K'],rtol=0,atol=1e-9)):
            raise ValueError('Sparse camera differs from scene')
        path=folder/'images'/im.name
        identity[im.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        if iid in training:
            with Image.open(path) as source:
                if source.size!=(camera.width,camera.height):
                    raise ValueError('Photo dimensions differ from camera')
                pixels[iid]=np.asarray(source.convert('RGB'))
    expected = [(Path(im.name).with_suffix('.png').name, identity[im.name])
                for i, (_, im) in enumerate(images) if i % 8 == 0]
    recorded = [(r['name'], r['reference_sha256']) for r in report.get('evaluation_images', [])]
    if recorded != expected:
        raise ValueError('Held-out reference identity mismatch')
    focus_id, focus_image = images[focus_index]
    focus_counts = Counter(o.point3D_id for o in focus_image.points2D if o.has_point3D())
    focus_tracks = {o.point3D_id:o.xy for o in focus_image.points2D
                    if o.has_point3D() and focus_counts[o.point3D_id] == 1}
    pairs,focus_pairs=defaultdict(list),defaultdict(list)
    counts=Counter();focus_points=[];recovered=[]
    for pid,point in sorted(model.points3D.items()):
        elements, ambiguous = unique_camera_elements(point.track.elements)
        if ambiguous and duplicate_policy == 'reject-track':
            counts['ambiguous_camera_track_rejected'] += 1
            continue
        if ambiguous:
            counts['tracks_with_ambiguous_images'] += 1
            counts['ambiguous_training_images_excluded'] += len(ambiguous & training)
            counts['ambiguous_training_observations_excluded'] += sum(
                e.image_id in ambiguous & training for e in point.track.elements)
        if not np.isfinite(point.error) or point.error>=4:
            counts['point_error_rejected']+=1;continue
        observed=[];seen=set();geometry=[]
        for elem in elements:
            iid=elem.image_id
            if iid not in training:
                continue
            if iid in seen:
                raise ValueError('Duplicate camera in track')
            seen.add(iid)
            im=model.images[iid];obs=im.points2D[elem.point2D_idx]
            if not obs.has_point3D() or obs.point3D_id!=pid:
                raise ValueError('Broken reciprocal track')
            xyz=im.cam_from_world()*point.xyz
            camera=model.cameras[im.camera_id]
            if not np.isfinite(xyz).all() or xyz[2]<=0:
                counts['behind_camera']+=1;continue
            projected=camera.calibration_matrix()@xyz;projected=projected[:2]/projected[2]
            residual = float(np.linalg.norm(projected-obs.xy))
            if not np.isfinite(obs.xy).all() or not np.isfinite(residual) or residual>2:
                counts['observation_error_rejected']+=1;continue
            color=sample_rgb(pixels[iid],obs.xy)
            if color is None:
                counts['sample_boundary_rejected']+=1;continue
            counts['sampled_training_observations']+=1
            observed.append((iid,color))
            if ambiguous:
                geometry.append(dict(image_name=im.name,point2D_index=int(elem.point2D_idx),
                                     residual_px=residual))
        if len(observed)<2:
            counts['insufficient_training_observations']+=1;continue
        counts['accepted_tracks']+=1
        if ambiguous:
            counts['recovered_tracks'] += 1
            recovered.append(dict(point_id=int(pid),point_error_px=float(point.error),
                                  excluded_images=sorted(names[i] for i in ambiguous),
                                  observations=geometry))
        in_focus = pid in focus_tracks
        if in_focus:
            elem = next((e for e in point.track.elements if e.image_id == focus_id), None)
            if (elem is None or focus_image.points2D[elem.point2D_idx].point3D_id != pid
                    or not np.array_equal(focus_image.points2D[elem.point2D_idx].xy, focus_tracks[pid])):
                raise ValueError('Broken focus reciprocal track')
            xyz = focus_image.cam_from_world() * point.xyz
            camera = model.cameras[focus_image.camera_id]
            xy = np.asarray(focus_tracks[pid])
            projected = camera.calibration_matrix() @ xyz
            in_focus = (np.isfinite(xyz).all() and xyz[2] > 0 and np.isfinite(xy).all()
                        and 0.5 <= xy[0] < camera.width - 0.5
                        and 0.5 <= xy[1] < camera.height - 0.5
                        and np.linalg.norm(projected[:2] / projected[2] - xy) <= 2)
            if not in_focus:
                counts['focus_geometry_rejected'] += 1
        for key,delta in training_pairs(observed,training):
            pairs[key].append(delta)
            if in_focus:focus_pairs[key].append(delta)
        if in_focus:
            focus_points.append(dict(point_id=pid,xy=np.asarray(focus_tracks[pid]).tolist(),
                                     training_observations=len(observed)))
    return dict(job=job,focus_image=focus_image.name,focus_dimensions=[views[focus_index]['width'],views[focus_index]['height']],
        training_images=[names[i] for i in sorted(training)],holdout_color_samples=0,
        counts=dict(counts),all_training=summarize_pairs(pairs,names),focus_training=summarize_pairs(focus_pairs,names),
        recovered_tracks=recovered,ambiguous_focus_tracks_excluded=sum(n>1 for n in focus_counts.values()),
        focus_points=focus_points,photo_sha256=identity,
        sparse_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((folder/'sparse').glob('*.bin'))},
        settings=dict(point_error_exclusive=4,observation_error_max=2,min_pair_tracks=20,
                      duplicate_policy=duplicate_policy,units='encoded RGB / 255',direction='second image minus first'),
        analysis_seconds=time.perf_counter()-start,pycolmap_version=pycolmap.__version__,
        limitation='Sparse feature colors mix illumination, reflectance, image processing and sampling errors; not an exposure or geometry estimate.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',type=Path,default=Path('.data'))
    p.add_argument('--job',required=True)
    p.add_argument('--focus-index',type=int,default=8)
    p.add_argument('--duplicate-policy',choices=['exclude-image','reject-track'],default='exclude-image')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    result=measure(args.data,args.job,args.focus_index,args.duplicate_policy)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ['job','counts','analysis_seconds']}))


if __name__=='__main__':main()
