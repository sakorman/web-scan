"""Publish measured audit counts without local photo, model or path contents."""
import argparse
from pathlib import Path
from scripts.benchmark_depth_overlap import read
from scripts.benchmark_depth_prior import digest, save

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, required=True)
args = parser.parse_args()
data = read(args.root/'report.json')
assert data['script_sha256']['candidate'] == digest('scripts/benchmark_depth_reprojection.py')
save('docs/benchmarks/depth-reprojection-audit.json', data)

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
fig, axes = plt.subplots(1, 2, figsize=(11, 4), layout='constrained')
variants = [v for v in data['scenes']['tum']['corrected']['variants'] if v['radius'] == .01]
for i, v in enumerate(variants):
    s = v['summary']
    for ax, keys in zip(axes, [('legacy_sensor_two', 'legacy_sensor_two_check_only'),
                             ('joint_check_three', 'joint_spatial_three', 'joint_far_three')]):
        counts = [s[k] for k in keys]
        positions = np.arange(len(keys)) + i*.35
        ax.bar(positions, counts, width=.35, color=['#286bb5','#74b7cf'][i], label=v['order'])
        for x, y in zip(positions, counts):
            ax.text(x, y+max(counts)*.025, str(y), ha='center', fontsize=10)
        ax.set_ylim(0, max(ax.get_ylim()[1], max(counts)*1.2))
axes[0].set_xticks([.175,1.175], ['Legacy 5% depth\n2 targets', 'Same loose rule\ncheck cells only'])
axes[1].set_xticks([.175,1.175,2.175], ['RGB + measured\n3-view check', '+ 3D distance\n<= 1% measured Z', '+ measured points\nfar from sparse seed'])
for ax in axes:
    ax.set_ylabel('Groups'); ax.legend(); ax.spines[['top','right']].set_visible(False)
axes[0].set_title('Historical loose counts (not validated new points)')
axes[1].set_title('Corrected common-view audit (source included)')
fig.suptitle('TUM desk · fixed 1% groups · criteria comparison, no training change')
fig.savefig('docs/screenshots/197-depth-reprojection-audit.png', dpi=150)
plt.close(fig)
