"""Rebuild figures and the public evidence bundle from frozen local outputs."""
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import numpy as np
from PIL import Image

root = Path('.data/benchmarks/depth-support-20261102')
old = Path('.data/benchmarks/depth-sensor-20261101')
read = lambda p: json.loads(p.read_text())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
metrics = read(root/'evaluation/support-evaluation.json')
cross = read(root/'masks/consistency.json')
sensor = read(old/'evaluation-v3/sensor-evaluation.json')
anchors = read(old/'sensor-v2/anchors.json')['views']
scores = read(root/'rgb-scores.json')
plt.rcParams.update({'font.size':10, 'figure.facecolor':'#f5f7fb','axes.facecolor':'#263449'})
error_cmap = plt.get_cmap('magma').copy();error_cmap.set_bad('#263449')
# First selected view, smallest retained fraction, largest unfiltered mean error.
chosen = ['0009','0041','0057']
fig, axes = plt.subplots(3,4,figsize=(14,8.5),layout='constrained')
for i,index in enumerate(chosen):
    row = next(v for v in metrics['views'] if v['name'].startswith(index))
    srow = next(v for v in sensor['views'] if v['name']==row['name'])
    crow = next(v for v in cross['scenes'][0]['views'] if v['name']==row['name'])
    view = next(v for v in anchors if v['name']==row['name'])
    with np.load(old/'evaluation-v3'/srow['map']) as s,np.load(root/'masks'/crow['map']) as c:
        check = s['check']; ref=s['sensor_m']; prior=s['prior_m']
        supported=c['valid'] & (c['support']>=2)
        error=np.where(check & np.isfinite(ref),np.abs(prior/ref-1),np.nan)
        axes[i,0].imshow(Image.open(view['image']));axes[i,0].set_title(f'TUM {index}: raw RGB')
        im=axes[i,1].imshow(error,vmin=0,vmax=.25,cmap=error_cmap,interpolation='nearest')
        axes[i,1].set_title(f'All check cells: {row["variants"]["all"]["retained"]["valid_prediction_metrics"]["mean_abs_rel"]:.2%} AbsRel')
        classes=np.where(check & supported,np.where(c['conflict']>0,2,1),0)
        axes[i,2].imshow(classes,vmin=0,vmax=2,cmap=ListedColormap(['#263449','#30c4bb','#ffa647']),interpolation='nearest')
        axes[i,2].set_title(f'Two supports: {row["variants"]["supported"]["coverage"]:.2%} retained')
        axes[i,3].imshow(np.where(supported,error,np.nan),vmin=0,vmax=.25,cmap=error_cmap,interpolation='nearest')
        axes[i,3].set_title(f'Retained: {row["variants"]["supported"]["retained"]["valid_prediction_metrics"]["mean_abs_rel"]:.2%} AbsRel')
    for ax in axes[i]: ax.set_xticks([]);ax.set_yticks([])
fig.colorbar(im,ax=axes[:,[1,3]],shrink=.65,label='AbsRel (display clipped at 25%; scores unclipped)')
fig.suptitle('Cross-view selection on frozen TUM depth priors\nTeal: supported without front conflict; orange: supported with conflict; dark: excluded / missing',fontsize=13)
fig.savefig('docs/screenshots/192-depth-support.png',dpi=130);plt.close(fig)

fig,axes=plt.subplots(2,3,figsize=(13,7),layout='constrained')
for i,index in enumerate(['0000','0080']):
    v=next(v for v in scores['views'] if v['view'].startswith(index))
    ref=Image.open(old/'jobs/tum-baseline/sfm/undistorted/images'/Path(v['view']).with_suffix('.jpg')).convert('RGB')
    ref.thumbnail((512,512),Image.Resampling.LANCZOS)
    axes[i,0].imshow(ref);axes[i,0].set_title(f'{index}: reference 512 × 384')
    for j,(work,kind,label) in enumerate([(old/'jobs/tum-baseline','quick','Previous RGB baseline'),(root/'jobs/tum-control','detail','New RGB control')],1):
        render=Image.open(work/'brush/eval_3000'/v['view']).convert('RGB')
        assert render.size==ref.size==(512,384)
        axes[i,j].imshow(render);axes[i,j].set_title(f'{label}\n{v[kind]["psnr"]:.3f} dB / {v[kind]["ssim"]:.4f} SSIM')
    for ax in axes[i]: ax.set_xticks([]);ax.set_yticks([])
fig.suptitle('Same-camera held-out renders · unchanged RGB training · 3000 steps\nFirst held-out frame and largest SSIM decrease; neither run uses depth support masks',fontsize=13)
fig.savefig('docs/screenshots/193-depth-support-control.png',dpi=130);plt.close(fig)

previous=read(Path('docs/benchmarks/depth-sensor.json'))
public={'config':read(root/'config.json'),'projection':'PyCOLMAP 3.13.0 RADIAL; native raw image pixels; no sensor resampling',
        'cross_view':cross,'sensor_selection':metrics,'eth3d_regression':read(root/'eth-regression.json'),
        'eth3d_frozen_baselines':previous['eth3d_reused'],
        'rgb_baseline':previous['baseline'],'rgb_control':read(root/'jobs/tum-control/result.json'),
        'rgb_scores':scores,'source_report_sha256':sha(Path('docs/benchmarks/depth-sensor.json')),
        'frozen_sensor_evaluation_sha256':sha(old/'evaluation-v3/sensor-evaluation.json'),
        'camera_and_prediction_identity':{'anchors_sha256':sha(old/'sensor-v2/anchors.json'),'analysis_sha256':sha(old/'analysis.json')},
        'script_sha256':{str(p):sha(p) for p in [Path('scripts/benchmark_depth_support.py'),Path('scripts/benchmark_depth_consistency.py'),Path('docs/benchmarks/depth-support-retrain.py')]},
        'validation':{'backend_tests':234,'frontend_tests':18,'typescript':'passed','build':'passed','browser':'both models loaded; photo 81 then indoor drag; no console warnings/errors', 'service':'health metadata and prepared sample detection temporarily missing in old process; idle restart restored both; cause unconfirmed'},
        'limitations':['Selected pixels are different subsets; depth values are unchanged.',
                       'Sensor scale is fitted outside check cells but uses sensor measurements; not RGB-only metric accuracy.',
                       'No point fusion, added Gaussians or learned regularization; no training-speed claim.',
                       'RGB control is one stochastic run with cached SfM; not an algorithm comparison.',
                       'Sensor holes and unselected geometry remain unvalidated.']}
Path('docs/benchmarks/depth-support.json').write_text(json.dumps(public,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print('RGB',scores['mean']);print('sensor',metrics['equal_view'])
