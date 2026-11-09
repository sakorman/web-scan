"""Summarize the interval control and the two RGB-only Brush regressions."""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image, ImageDraw

from scripts.benchmark_depth_prior import digest, save
from scripts.benchmark_gmdepth_cycle import read

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, required=True)
args = parser.parse_args()
root = args.root
output = Path('docs/benchmarks/gmdepth-bidir.json')
screens = Path('docs/screenshots')
out = dict(baseline_commit='356b0f43ecadfed8aa0ed1c1e8ffdf479631d282', scenes={}, rgb_controls={})
for name in ('pipes','office','tum'):
    meta = read(root/name/'bidir.json')
    assert meta['status'] == 'complete' and meta['device'] == 'mps'
    out['scenes'][name] = meta

scores = read(root/'tum-score/sensor.json')
assert scores['prediction_sha256'] == digest(root/'tum/bidir.json')
out['tum_sensor'] = scores
for name in ('pipes', 'office'):
    work = root/'verified-jobs'/(name+'-control')
    previous = Path('.data/benchmarks/gmdepth-20261107/verified-jobs')/(name+'-control')
    d = read(work/'result.json')
    report = d['report']
    images = report['evaluation_images']
    def identity(report):
        return [(r['name'],r['width'],r['height'],r['reference_sha256']) for r in report['evaluation_images']]
    assert identity(report) == identity(read(previous/'result.json')['report'])
    assert read(work/'artifacts/cameras.json') == read(previous/'artifacts/cameras.json')
    assert d['status'] == 'complete' and report['evaluation_verified'] and digest(work/'artifacts/gaussians.ply') == d['model_sha256']
    out['rgb_controls'][name] = {k:d[k] for k in ('config','sfm_reused','sfm_seconds','training_export_seconds',
                                                   'end_to_end_seconds','model_bytes','model_sha256','source_result_sha256')}
    out['rgb_controls'][name]['sfm'] = {k:d['sfm'][k] for k in ('input_images','registered_images','unregistered_images','camera_model','original_cameras')}
    out['rgb_controls'][name]['report'] = {k:report[k] for k in ('training_seed','steps','resolution','gaussians','training_views','evaluation_views','evaluation','evaluation_images')}
    row = images[0]
    with Image.open(work/'sfm/undistorted/images'/(Path(row['name']).stem+'.jpg')) as im:
        ref = im.convert('RGB').resize((row['width'],row['height']), Image.Resampling.LANCZOS)
    path = work/'brush'/('eval_'+str(report['steps']))/row['name']
    assert digest(path) == row['sha256']
    with Image.open(path) as im:
        render = im.convert('RGB')
    canvas = Image.new('RGB',(ref.width*2,ref.height+26),'white')
    canvas.paste(ref,(0,26)); canvas.paste(render,(ref.width,26))
    ImageDraw.Draw(canvas).text((8,6),name+' | held-out photo (left) / RGB-only control (right)',fill='black')
    canvas.save(screens/('202-bidir-'+name+'.png'))
save(output, out)
names = ['pipes','office','tum']
fig, ax = plt.subplots(figsize=(7.6,3.6),layout='constrained')
for offset, field, label, color in [(-.17, 'reverse_target_range', 'Separate reverse: target interval','#cf6637'),
                                     (.17,'reverse_matched_range', 'Separate reverse: shared interval','#266fb4')]:
    values = [100*max(v[field]['max_relative'] for v in out['scenes'][n]['views']) for n in names]
    ax.bar(np.arange(3)+offset, values, width=.34, label=label, color=color)
ax.set_xticks(range(3),['ETH3D pipes','ETH3D office','TUM']); ax.set_yscale('log')
ax.set_ylabel('Maximum relative difference (%) · log scale')
ax.set_title('Reverse maps compared with the built-in bidirectional output')
ax.legend(fontsize=8,loc='center left'); ax.spines[['top','right']].set_visible(False)
fig.savefig(screens/'201-gmdepth-bidir.png',dpi=150); plt.close(fig)
