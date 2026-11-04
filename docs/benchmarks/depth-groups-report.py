"""Verify frozen real-photo inputs and publish group-count evidence."""
import argparse
from pathlib import Path
import numpy as np

from scripts.benchmark_depth_overlap import read
from scripts.benchmark_depth_prior import digest, save
from webscan.brush import read_ply

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, required=True)
args = parser.parse_args()
prior = read(Path('docs/benchmarks/depth-overlap.json'))
output = {'config': read(args.root/'config.json'), 'scenes': {}, 'frozen_rgb_baselines': prior['rgb_baselines']}
for scene in ('pipes-baseline', 'office-baseline', 'tum'):
    baseline = prior['diagnostics'][scene if scene != 'tum' else 'tum-new']
    fresh = read(args.root/(scene+'-overlap.json'))
    assert fresh['views'] == baseline['views'] and fresh['pooled'] == baseline['pooled']
    for key in ('result_sha256', 'sparse_sha256', 'training_points_sha256', 'consistency_sha256', 'sensor_sha256'):
        assert fresh[key] == baseline[key]
    work = (Path('.data/benchmarks/depth-overlap-20261103/jobs/tum-baseline') if scene == 'tum' else
            Path('.data/benchmarks/depth-consistency-20261101/jobs')/scene)
    result = read(work/'result.json')
    for row in result['inputs']:
        assert digest(work/'input'/row['name']) == row['sha256']
    model = work/'artifacts/gaussians.ply'
    _, points = read_ply(model, expected_sh_degree=3)
    assert len(points) == result['report']['gaussians']
    assert digest(model) == prior['rgb_baselines'][scene.split('-')[0]]['model_sha256']
    groups = read(args.root/scene/'groups.json')
    variants = []
    for v in groups['variants']:
        path = args.root/scene/v['map']
        assert digest(path) == v['map_sha256']
        with np.load(path, allow_pickle=False) as arrays:
            assert len(arrays['ids']) == v['summary']['samples']
            assert len(arrays['leaders']) == v['summary']['groups']
            assert np.all(np.bincount(arrays['ids']) == [r['samples'] for r in v['groups']])
        variants.append({k: v[k] for k in ('radius', 'order', 'summary', 'map_sha256')})
    output['scenes'][scene] = dict(variants=variants, grouping_seconds=groups['seconds'],
        extraction_seconds=fresh['seconds'], samples_sha256=groups['samples_sha256'],
        baseline_counts_and_source_files_verified=True)
output['script_sha256'] = {f: digest(f) for f in ['scripts/benchmark_depth_groups.py','scripts/benchmark_depth_overlap.py']}
save('docs/benchmarks/depth-groups.json', output)

# Plot counts only; no new rendering or training quality comparison.
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig, axes = plt.subplots(1, 3, figsize=(12, 4), layout='constrained')
for ax, (key, data) in zip(axes, output['scenes'].items()):
    rows = [v for v in data['variants'] if v['radius'] == .01]
    for i, row in enumerate(rows):
        v = row['summary']
        counts = [v['samples'], v['groups'], v['diverse_three']]
        ax.bar(np.arange(3)+i*.35, counts, width=.35, label=row['order'],
               color=['#286bb5','#74b7cf'][i])
        for x,y in zip(np.arange(3)+i*.35,counts):
            ax.text(x,y*1.08,str(y),ha='center',fontsize=9)
    ax.set_xticks([.175,1.175,2.175],['Samples','Groups','3 views + angle'])
    ax.set_yscale('log');ax.set_ylim(10,45000);ax.set_title(key);ax.legend(loc='upper right',fontsize=8)
axes[0].set_ylabel('Count (log scale)')
fig.suptitle('1% radius · fixed candidate set · spatial groups are not validated new points')
fig.savefig('docs/screenshots/196-depth-groups.png', dpi=140)
plt.close(fig)
