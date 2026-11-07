"""Summarize the frozen paired-depth probe and RGB-only controls."""
import argparse,json
from pathlib import Path
import numpy as np
from scripts.benchmark_depth_prior import digest,save

parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True)
root=parser.parse_args().root
out=dict(baseline_commit='c67c550404c24401ee2cd6af42f8a290e08c7a69',scenes={},controls={},
         script_sha256=digest('scripts/benchmark_gmdepth.py'),scope='paired depth feasibility, no Gaussian initialization change')
for name in ('pipes','office','tum'):
    score=json.loads((root/(name+'-final')/'scores.json').read_text())
    pairs=json.loads((root/name/'pairs.json').read_text())
    out['scenes'][name]=dict(scores=score,pairs_sha256=digest(root/name/'pairs.json'),
        model_sha256=pairs['model_sha256'],sparse_sha256=pairs['sparse_sha256'],
        pairs=[dict(source=r['source']['name'],target=r['target']['name'],source_sha256=r['source']['sha256'],
                    target_sha256=r['target']['sha256'],network_K=r['K'],target_from_source=r['pose'],
                    size=r['size'],near=r['near'],far=r['far']) for r in pairs['records']])
    if name!='tum':
        work=root/'verified-jobs'/(name+'-control'); d=json.loads((work/'result.json').read_text())
        source=Path('.data/benchmarks/depth-consistency-20261101/jobs')/(name+'-baseline')
        previous=json.loads((source/'result.json').read_text())
        identity=lambda report:[(v['name'],v['reference_sha256'],v['width'],v['height']) for v in report['evaluation_images']]
        assert identity(d['report'])==identity(previous['report'])
        assert json.loads((work/'artifacts/cameras.json').read_text())==json.loads((source/'artifacts/cameras.json').read_text())
        assert digest(work/'artifacts/gaussians.ply')==d['model_sha256']
        for row in d['inputs']:assert digest(work/'input'/row['name'])==row['sha256']
        out['controls'][name]={k:d[k] for k in ('config','sfm','sfm_reused','sfm_seconds','training_export_seconds','end_to_end_seconds','model_bytes','model_sha256')}
        out['controls'][name]['report']={k:d['report'][k] for k in ('steps','resolution','gaussians','training_views','evaluation_views','evaluation','evaluation_images','engine_version','training_seed') if k in d['report']}
        out['controls'][name]['camera_reference_verified']=True
cpu=np.load(next((root/'pipes-cpu').glob('*.npy'))); mps=np.load(next((root/'pipes-mps').glob('*.npy')))
out['cpu_mps_parity']=dict(mean_relative=float(np.mean(abs(mps/cpu-1))),max_relative=float(np.max(abs(mps/cpu-1))))
views=out['scenes']['tum']['scores']['views']
out['sensor_summary']={k:dict(mean_view_abs_rel=float(np.mean([v['sensor'][k]['valid_prediction_metrics']['mean_abs_rel'] for v in views])),
 reference_samples=sum(v['sensor'][k]['reference_samples'] for v in views),invalid_predictions=sum(v['sensor'][k]['invalid_predictions'] for v in views)) for k in ('gmdepth','monocular')}
out['sensor_summary']['outside_network']=sum(v['sensor']['outside_network'] for v in views)
save('docs/benchmarks/gmdepth.json',out)

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig,ax=plt.subplots(figsize=(10,4),layout='constrained'); x=np.arange(len(views))
for i,(key,label) in enumerate([('monocular','Frozen monocular prior'),('gmdepth','GMDepth two-view')]):
    ax.bar(x+i*.35,[100*v['sensor'][key]['valid_prediction_metrics']['mean_abs_rel'] for v in views],.35,label=label,color=['#7ab6cc','#246bb8'][i])
ax.set_xticks(x+.175,[v['name'][:4] for v in views]);ax.set_ylabel('Mean absolute relative error (%)');ax.set_xlabel('Reference training image')
ax.set_title('TUM · same raw-camera check pixels · lower is better');ax.legend();ax.spines[['top','right']].set_visible(False)
fig.savefig('docs/screenshots/198-gmdepth-errors.png',dpi=150);plt.close(fig)
from PIL import Image,ImageDraw
for name in ('pipes','office'):
    work=root/'verified-jobs'/(name+'-control'); report=json.loads((work/'result.json').read_text())['report']
    row=report['evaluation_images'][0]; file=row['name']
    with Image.open(work/'sfm/undistorted/images'/(Path(file).stem+'.jpg')) as im:
        ref=im.convert('RGB').resize((row['width'],row['height']),Image.Resampling.LANCZOS)
    with Image.open(work/'brush'/('eval_'+str(report['steps']))/file) as im: render=im.convert('RGB')
    canvas=Image.new('RGB',(ref.width*2,ref.height+28),'white');canvas.paste(ref,(0,28));canvas.paste(render,(ref.width,28))
    ImageDraw.Draw(canvas).text((8,6),name+' | held-out reference (left) / RGB control (right)',fill='black')
    canvas.save('docs/screenshots/199-gmdepth-'+name+'-control.png')
