"""Two serial RGB-only Brush controls; depth probes are never injected."""
import argparse
import json,shutil,time
from pathlib import Path
from scripts.benchmark_depth_prior import digest,save
from scripts.benchmark_exports import write_comparison_artifacts
from webscan.brush import train,read_ply
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root',type=Path,required=True)
root=parser.parse_args().root
for name in ('pipes','office'):
 source=Path('.data/benchmarks/depth-consistency-20261101/jobs')/(name+'-baseline')
 work=root/'verified-jobs'/(name+'-control');work.mkdir(parents=True,exist_ok=False)
 result=json.loads((source/'result.json').read_text())
 config=json.loads(Path('.data/benchmarks/depth-consistency-20261101/config.json').read_text())['training']
 start=time.monotonic()
 shutil.copytree(source/'sfm',work/'sfm')
 shutil.copytree(source/'input',work/'input')
 scene=json.loads((work/'sfm/scene.json').read_text())
 for v in scene['views']:v['image']=str((work/'sfm/undistorted/images'/v['image_name']).resolve())
 for v in result['inputs']:assert digest(work/'input'/v['name'])==v['sha256']
 write_comparison_artifacts(work,scene,{})
 t=time.monotonic();report=train(scene,work,work/'artifacts',config,lambda *_:None);elapsed=time.monotonic()-t
 write_comparison_artifacts(work,scene,report)
 _,points=read_ply(work/'artifacts/gaussians.ply',expected_sh_degree=3)
 assert len(points)==report['gaussians']
 save(work/'result.json',dict(status='complete',source_result_sha256=digest(source/'result.json'),config=config,
   sfm=result['sfm'],inputs=result['inputs'],sfm_reused=True,sfm_seconds=0,training_export_seconds=elapsed,
   end_to_end_seconds=time.monotonic()-start,report=report,model_bytes=(work/'artifacts/gaussians.ply').stat().st_size,
   model_sha256=digest(work/'artifacts/gaussians.ply')))
 print(name,elapsed,len(points),flush=True)
