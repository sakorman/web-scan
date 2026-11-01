"""Frozen report and figures; run with the isolated Matplotlib dependency path."""
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

root = Path('.data/benchmarks/depth-sensor-20261101')
read = lambda path: json.loads(path.read_text())
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
evaluation = read(root/'evaluation-v3/sensor-evaluation.json')
anchors = read(root/'sensor-v2/anchors.json')['views']
scores = read(root/'baseline-scores.json')
work = root/'jobs/tum-baseline'
baseline = read(work/'result.json')
plt.rcParams.update({'font.size':10, 'figure.facecolor':'#f5f7fb', 'axes.facecolor':'#152335'})

# First valid selected frame, largest median error, and final selected frame.
chosen = ['0009','0057','0089']
fig, axes = plt.subplots(3,4,figsize=(14,8.6),layout='constrained')
depth_cmap = plt.get_cmap('viridis').copy(); depth_cmap.set_bad('#263449')
error_cmap = plt.get_cmap('magma').copy(); error_cmap.set_bad('#263449')
for row,index in enumerate(chosen):
    result = next(v for v in evaluation['views'] if v['name'].startswith(index))
    view = next(v for v in anchors if v['name']==result['name'])
    arrays = np.load(root/'evaluation-v3'/result['map'])
    reference, predicted, check = arrays['sensor_m'], arrays['prior_m'], arrays['check']
    valid = np.isfinite(reference) & (reference>0)
    error = np.where(check & valid, np.abs(predicted/reference-1), np.nan)
    axes[row,0].imshow(Image.open(view['image'])); axes[row,0].set_title(f'TUM {index}: original RGB')
    axes[row,1].imshow(np.ma.masked_invalid(reference),vmin=.4,vmax=4,cmap=depth_cmap,interpolation='nearest')
    axes[row,1].set_title('Registered sensor depth')
    depth_image = axes[row,2].imshow(np.ma.masked_invalid(predicted),vmin=.4,vmax=4,cmap=depth_cmap,interpolation='nearest')
    axes[row,2].set_title('SfM-aligned relative prediction')
    error_image = axes[row,3].imshow(np.ma.masked_invalid(error),vmin=0,vmax=.25,cmap=error_cmap,interpolation='nearest')
    axes[row,3].set_title(f'Check cells: median {result["prior"]["valid_prediction_metrics"]["median_abs_rel"]:.2%}')
    for ax in axes[row]: ax.set_xticks([]); ax.set_yticks([])
fig.colorbar(depth_image,ax=axes[:,1:3],shrink=.65,label='Camera Z (metres); display clipped to 0.4–4 m')
fig.colorbar(error_image,ax=axes[:,3],shrink=.65,label='Absolute relative error; display clipped at 25%')
fig.suptitle('TUM RGB-D desk · sensor validation on raw image grid\nDark cells: missing sensor or excluded fit region; no point injection',fontsize=14)
fig.savefig('docs/screenshots/190-sensor-depth-check.png',dpi=130)
plt.close(fig)

fig, axes = plt.subplots(2,2,figsize=(10,8.1),layout='constrained')
for i,index in enumerate(['0000','0064']):
    score = next(v for v in scores['views'] if v['view'].startswith(index))
    ref = Image.open(work/'sfm/undistorted/images'/Path(score['view']).with_suffix('.jpg')).convert('RGB')
    ref.thumbnail((512,512),Image.Resampling.LANCZOS)
    render = Image.open(work/'brush/eval_3000'/score['view']).convert('RGB')
    assert ref.size==render.size==(512,384)
    axes[i,0].imshow(ref); axes[i,1].imshow(render)
    axes[i,0].set_title(f'{index}: undistorted reference, 512 × 384')
    axes[i,1].set_title(f'RGB-only baseline: {score["quick"]["psnr"]:.3f} dB / {score["quick"]["ssim"]:.4f} SSIM')
    for ax in axes[i]: ax.set_xticks([]); ax.set_yticks([])
fig.suptitle('Same-camera held-out RGB renders · 3000 steps · SfM used all 96 photos',fontsize=13)
fig.savefig('docs/screenshots/191-sensor-depth-baseline.png',dpi=130)
plt.close(fig)

summary={}
for method in ('prior','constant','sfm_check_anchors'):
    rows=[v[method] for v in evaluation['views']]
    summary[method]={'passed_views':sum(v['passed'] for v in rows),
                     'valid_sensor_samples':sum(v['reference_samples'] for v in rows),
                     'missing_sensor_samples':sum(v['missing_reference'] for v in rows),
                     'invalid_predictions':sum(v['invalid_predictions'] for v in rows),
                     'view_equal_mean':{k:float(np.mean([v['valid_prediction_metrics'][k] for v in rows]))
                                        for k in ('median_abs_rel','p90_abs_rel','mean_abs_rel','rmse_m')},
                     'within_10_percent_view_mean':float(np.mean([v['within_10_percent'] for v in rows])),
                     'delta_1_25_view_mean':float(np.mean([v['delta_1_25'] for v in rows]))}
eth=read(Path('docs/benchmarks/depth-consistency.json'))
public={'config':read(root/'config.json'),'sensor_manifest':read(root/'sensor-v2/sensor.json'),
        'baseline':baseline,'baseline_scores':scores,'single_view':read(root/'analysis.json'),
        'sensor_evaluation':evaluation,'summary':summary,
        'cpu_verification':{'previous_seconds':read(root/'evaluation-v2/sensor-evaluation.json')['seconds'],'final_seconds':evaluation['seconds'],'all_scores_and_npz_arrays_equal':True},
        'eth3d_reused':{'source_report':'depth-consistency.json','sha256':sha(Path('docs/benchmarks/depth-consistency.json')),
                        'runs':eth['baseline_runs'],'scores':eth['baseline_scores'],
                        'verification':'Input hashes and complete SH3 counts rechecked; no retraining or new ETH3D scores'},
        'artifacts':{str(p.relative_to(work)):sha(p) for p in [work/'artifacts/gaussians.ply',work/'artifacts/cameras.json',work/'artifacts/report.json']},
        'script_sha256':{str(p):sha(p) for p in [Path('scripts/benchmark_sensor_depth.py'),Path('scripts/benchmark_depth_prior.py'),Path('docs/benchmarks/depth-sensor-run.py')]},
        'local_failures':['First preparation refused one unmatched RGB frame; final implementation records it and keeps the 20ms limit.',
                          'First evaluation failed JSON serialization of a NumPy boolean; native bool conversion fixed it and integration regression passes.'],
        'validation':{'backend_tests':221,'frontend_tests':18,'typescript':'passed','build':'passed','matplotlib':matplotlib.__version__}}
Path('docs/benchmarks/depth-sensor.json').write_text(json.dumps(public,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
Path('docs/benchmarks/depth-sensor-config.json').write_text((root/'config.json').read_text())
print(json.dumps(summary,indent=2))
print('RGB baseline',scores['mean'])
