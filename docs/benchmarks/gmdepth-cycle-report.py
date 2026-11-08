"""Publish the reciprocal-depth audit summary without exposing local paths."""
import argparse
import json
from pathlib import Path

import numpy as np

from scripts.benchmark_depth_prior import digest, save

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, required=True)
parser.add_argument('--json', type=Path, default=Path('docs/benchmarks/gmdepth-cycle.json'))
parser.add_argument('--plot', type=Path, default=Path('docs/screenshots/200-gmdepth-cycle.png'))
args = parser.parse_args()
root = args.root
out = dict(
    baseline_commit='0f211294a97f415735e0f0b07b03c211085191db',
    report_script_sha256=digest(__file__),
    cycle_script_sha256=digest('scripts/benchmark_gmdepth_cycle.py'),
    hypothesis='reciprocal RGB depth agreement can screen reliable regions without changing Gaussian initialization',
    scope='RGB/SfM reciprocal audit; TUM sensor scores are held-out diagnostics; no point injection',
    config=json.loads((root/'config.json').read_text()),
    scenes={},
)
for name in ('pipes', 'office', 'tum'):
    audit = json.loads((root/'run'/(name+'-audit')/'audit.json').read_text())
    total = sum(v['check_samples'] for v in audit['views'])
    counts = {k: sum(v['counts'][k] for v in audit['views'])
              for k in ('supported', 'occlusion_candidate', 'front_depth_conflict', 'roundtrip_failed', 'low_parallax')}
    out['scenes'][name] = dict(audit_sha256=digest(root/'run'/(name+'-audit')/'audit.json'),
                               views=len(audit['views']), check_samples=total, counts=counts,
                               coverage=float(counts['supported']/total), seconds=audit['seconds'])
score = json.loads((root/'run/tum-score-fixed/scores.json').read_text())
regions = {}
for region in ('all', 'retained', 'rejected'):
    regions[region] = dict(grid_samples=int(sum(v['regions'][region]['grid_samples'] for v in score['views'])))
    for method in ('gmdepth', 'monocular'):
        values = [v['regions'][region][method]['valid_prediction_metrics']['mean_abs_rel'] for v in score['views']]
        regions[region][method] = dict(mean_view_abs_rel=float(np.mean(values)),
                                       median_view_abs_rel=float(np.median(values)), views=len(values))
out['tum_sensor_regions'] = regions
out['tum_sensor_score_sha256'] = digest(root/'run/tum-score-fixed/scores.json')
out['upstream'] = dict(unimatch_commit='943a99006950ab6c84ea72a72beb147fdd03c19a',
                       weights_sha256='d3d1efb5d1615404828574b6bad63e138c8f4fd8077557392b18b57324982b49')
save(args.json, out)

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
labels = ['All', 'Reciprocal\nsupported', 'Rejected']
gm = [100*regions[k]['gmdepth']['mean_view_abs_rel'] for k in ('all', 'retained', 'rejected')]
mono = [100*regions[k]['monocular']['mean_view_abs_rel'] for k in ('all', 'retained', 'rejected')]
x = np.arange(3); fig, ax = plt.subplots(figsize=(8, 4), layout='constrained')
ax.bar(x-.18, mono, .36, label='Frozen monocular prior', color='#7ab6cc')
ax.bar(x+.18, gm, .36, label='GMDepth two-view', color='#246bb8')
ax.set_xticks(x, labels); ax.set_ylabel('Mean absolute relative error (%)')
ax.set_title('TUM · reciprocal depth screen · lower is better'); ax.legend()
ax.spines[['top', 'right']].set_visible(False); fig.savefig(args.plot, dpi=150); plt.close(fig)
