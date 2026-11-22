"""Serial, fixed-camera test of excluding ambiguous SfM tracks from Brush seeds."""
import argparse
import json
import shutil
import struct
import time
from pathlib import Path

import numpy as np
from scripts.benchmark_exports import load_adapter, sha, write_comparison_artifacts
from scripts.benchmark_track_filter import prepare, point_records

SOURCES={'pipes':'5e45df7c1bfa4af6ba342326ca2fe407','office':'4533f0aa28a448bd9afb5983e6472de2'}
CONFIG=dict(backend='brush',steps=3000,max_points=150000,resolution=512,camera_mode='shared',evaluation_mode='holdout')
ORDER=[(s,v,r) for v,r,scenes in [('baseline',1,('pipes','office')),('candidate',1,('pipes','office')),
                                ('candidate',2,('office','pipes')),('baseline',2,('office','pipes'))] for s in scenes]


def save(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    args=p.parse_args();root=args.root.resolve();root.mkdir(parents=True,exist_ok=False)
    adapter_file=root/'brush.py';shutil.copy2('webscan/brush.py',adapter_file)
    binary=Path('.tools/brush/brush_app').resolve()
    plan=dict(sources=SOURCES,config=CONFIG,order=ORDER,seed=42,engine_sha256=sha(binary),
              adapter_sha256=sha(adapter_file),filter_sha256=sha(Path('scripts/benchmark_track_filter.py')),
              required='Each scene mean PSNR delta >= 0.2 dB and mean SSIM delta >= 0; every pair PSNR >= -0.2 and SSIM >= -0.005. Screening only, not a statistical guarantee or default adoption.',
              hypothesis='Removing all points with duplicate image IDs in their tracks improves seed reliability without pose or image changes.',
              cache='Frozen SfM, fresh Brush processes, Metal/system caches not flushed; serial GPU, no browser rendering.')
    save(root/'plan.json',plan)
    results=[]
    for name,variant,repeat in ORDER:
        work=root/'jobs'/f'{name}-{variant}-{repeat}';work.mkdir(parents=True)
        start=time.monotonic();source=Path('.data/jobs')/SOURCES[name]
        shutil.copytree(source/'sfm/undistorted/images',work/'sfm/undistorted/images')
        sparse=work/'sfm/undistorted/sparse'
        if variant=='candidate':
            filtering=prepare(source/'sfm/undistorted/sparse',sparse)
        else:
            shutil.copytree(source/'sfm/undistorted/sparse',sparse)
            count,=struct.unpack_from('<Q',(sparse/'points3D.bin').read_bytes())
            filtering=dict(input_points=count,retained_points=count,removed_point_ids=[],cleared_observations=0)
        scene=json.loads((source/'sfm/scene.json').read_text())
        for view in scene['views']:
            view['image']=str(work/'sfm/undistorted/images'/view['image_name'])
        # Preserve the source normalization and camera frame, even after filtering.
        seed=[struct.unpack('<QdddBBBdQ',record[:51]) for _,_,record in point_records((sparse/'points3D.bin').read_bytes())]
        scene['points']=((np.array([x[1:4] for x in seed],dtype=np.float32)-np.array(scene['normalization']['center']))/scene['normalization']['scale']).tolist()
        scene['colors']=(np.array([x[4:7] for x in seed],dtype=np.float32)/255).tolist()
        scene['initialization_filter']=filtering
        image_hashes={f.name:sha(f) for f in sorted((work/'sfm/undistorted/images').iterdir()) if f.is_file()}
        assert sha(sparse/'cameras.bin')==sha(source/'sfm/undistorted/sparse/cameras.bin')
        prepared=time.monotonic()-start
        print('Start',work.name,filtering['retained_points'],'seeds',flush=True)
        adapter=load_adapter(adapter_file);adapter.executable=lambda:binary
        training=time.monotonic()
        report=adapter.train(scene,work,work/'artifacts',CONFIG,lambda *_:None)
        training=time.monotonic()-training
        write_comparison_artifacts(work,scene,report)
        _,points=adapter.read_ply(work/'artifacts/gaussians.ply',expected_sh_degree=3)
        assert len(points)==report['gaussians']
        result=dict(name=work.name,scene=name,variant=variant,repeat=repeat,filtering=filtering,
                    image_hashes=image_hashes,cameras_sha256=sha(work/'artifacts/cameras.json'),
                    sparse_sha256={f.name:sha(f) for f in sparse.glob('*.bin')},
                    source_job=SOURCES[name],input_images=scene['input_images'],registered_images=scene['registered_images'],
                    failed_views=scene['unregistered_images'],camera=scene['views'][0]['original_camera'],
                    sfm_reused=True,sfm_seconds=0,prepare_seconds=prepared,training_export_seconds=training,
                    reuse_pipeline_seconds=time.monotonic()-start,model_bytes=(work/'artifacts/gaussians.ply').stat().st_size,report=report)
        results.append(result);save(work/'result.json',result);save(root/'results.json',results)
        print('Complete',work.name,round(training,3),'s',report['gaussians'],'splats',flush=True)
    for name in SOURCES:
        group=[r for r in results if r['scene']==name]
        assert len({r['cameras_sha256'] for r in group})==1
        assert all(r['image_hashes']==group[0]['image_hashes'] for r in group)
    save(root/'complete.json',dict(runs=len(results),fixed_cameras_and_photos=True))


if __name__=='__main__':main()
