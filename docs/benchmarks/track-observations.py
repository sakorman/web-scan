"""Compare both color-audit policies on preserved and new real reconstructions."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--runs',type=Path,required=True);p.add_argument('--work-dir',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
args=p.parse_args();args.work_dir.mkdir(parents=True,exist_ok=False)
plan=json.loads(Path('docs/benchmarks/track-observations-plan.json').read_text())
runs=json.loads(args.runs.read_text());assert [r['scene'] for r in runs]==plan['new_order']
result=dict(plan=plan,scenes={})
for run in runs:
 scene=run['scene'];audits={}
 for label,jid in [('baseline',plan['sources'][scene]),('new',run['job_id'])]:
  audits[label]={}
  for policy in plan['policies']:
   out=args.work_dir/f'{scene}-{label}-{policy}.json'
   subprocess.run([sys.executable,'scripts/benchmark_track_color.py','--job',jid,
                   '--duplicate-policy',policy,'--output',str(out)],check=True)
   audits[label][policy]=json.loads(out.read_text())
  geom=args.work_dir/f'{scene}-{label}-geometry.json'
  subprocess.run([sys.executable,'docs/benchmarks/track-observations-geometry.py',
                  '--audit',str(args.work_dir/f'{scene}-{label}-exclude-image.json'),
                  '--output',str(geom)],check=True)
  audits[label]['geometry']=json.loads(geom.read_text())
 for policy in [*plan['policies'],'geometry']:
  a,b=[{k:v for k,v in audits[label][policy].items() if k not in ('job','analysis_seconds')} for label in ('baseline','new')]
  assert a==b,'Cached input/color audit changed'
 old,new=[audits['baseline'][policy] for policy in plan['policies']]
 assert new['counts']['accepted_tracks']-old['counts']['accepted_tracks']==len(new['recovered_tracks'])
 w,h=new['focus_dimensions'];grid=[[0]*4 for _ in range(4)]
 for pt in new['focus_points']:
  x,y=pt['xy'];grid[min(3,int(y/h*4))][min(3,int(x/w*4))]+=1
 result['scenes'][scene]=dict(run=run,audits=audits,unchanged_cached_audit=True,focus_grid_4x4=grid)
# pycolmap ran in subprocesses; scoring loads Torch only in this process.
import torch
from scripts.compare_repeats import summarize
from webscan.brush import read_ply

torch.set_num_threads(4)
for scene,entry in result['scenes'].items():
 jobs=[plan['sources'][scene],entry['run']['job_id']]
 entry['quality']=summarize(Path('.data'),jobs,512)
 for jid in jobs:read_ply(Path('.data/jobs')/jid/'artifacts/gaussians.ply',expected_sh_degree=3)
 print(scene,entry['quality']['mean'],flush=True) if 'mean' in entry['quality'] else print(scene,'scored',flush=True)
args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
