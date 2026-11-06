"""Reproduce the loose audit, then verify corrected criteria on frozen RGB jobs."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from scripts.benchmark_depth_overlap import read
from scripts.benchmark_depth_prior import digest, save
from webscan.brush import read_ply

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, required=True, help='New output directory')
args = parser.parse_args()
args.root.mkdir(parents=True, exist_ok=False)
baseline_commit = '3142c6be9a1a25252e3c53848e1c5e5bfc6fca0d'
old = args.root/'baseline.py'
old.write_bytes(subprocess.check_output(['git', 'show', baseline_commit+':scripts/benchmark_depth_reprojection.py']))
fixed = read(Path('docs/benchmarks/depth-groups.json'))['frozen_rgb_baselines']
group_root = Path('.data/benchmarks/depth-groups-20261104/verified')
output = dict(baseline_commit=baseline_commit, frozen_rgb_baselines=fixed, scenes={},
              protocol='all six fixed group variants; no new training; compare criteria, not quality or speed')
for label in ('pipes', 'office', 'tum'):
    is_tum = label == 'tum'
    root = Path('.data/benchmarks/depth-overlap-20261103' if is_tum else '.data/benchmarks/depth-consistency-20261101')
    job = label+'-baseline'
    group = 'tum' if is_tum else job
    work = root/'jobs'/job
    anchors = root/('sensor/anchors.json' if is_tum else 'anchors/anchors.json')
    common = ['--samples', str(group_root/(group+'-samples')), '--groups', str(group_root/group),
              '--anchors', str(anchors), '--analysis', str(root/'analysis.json'), '--predictions', str(root/'predictions')]
    old_output = args.root/(label+'-legacy.json')
    legacy = [sys.executable, str(old), *common, '--job', job, '--output', str(old_output)]
    new_output = args.root/(label+'-audit.json')
    candidate = [sys.executable, 'scripts/benchmark_depth_reprojection.py', *common, '--work', str(work),
                 '--consistency', str(root/('masks/consistency.json' if is_tum else 'cross-view-v2/consistency.json')),
                 '--output', str(new_output)]
    if is_tum:
        scale = read(root/'evaluation/sensor-evaluation.json')['scale_m_per_sfm_unit']
        legacy += ['--sensor-manifest', str(root/'sensor/sensor.json'), '--sensor-scale', str(scale)]
        candidate += ['--sensor-manifest', str(root/'sensor/sensor.json'),
                      '--sensor-evaluation', str(root/'evaluation/sensor-evaluation.json')]
    for stage, command in [('legacy', legacy), ('audit', candidate)]:
        with (args.root/(label+'-'+stage+'.log')).open('w') as log:
            subprocess.run(command, env={**os.environ, 'PYTHONPATH': '.'}, stdout=log, stderr=subprocess.STDOUT, check=True)
    a, b = read(old_output), read(new_output)
    historical = read(Path('.data/benchmarks/depth-reprojection-20261105')/(label+'.json'))
    assert a['variants'] == historical['variants']
    for av, bv in zip(a['variants'], b['variants'], strict=True):
        assert (av['radius'], av['order']) == (bv['radius'], bv['order'])
        assert av['summary']['groups'] == bv['summary']['groups']
        assert av['summary']['groups_with_two_support'] == bv['summary']['legacy_depth_only_two']
        if is_tum:
            assert av['summary']['sensor_groups_with_two_support'] == bv['summary']['legacy_sensor_two']
    result = read(work/'result.json')
    model = work/'artifacts/gaussians.ply'
    _, points = read_ply(model, expected_sh_degree=3)
    assert len(points) == result['report']['gaussians']
    assert digest(model) == b['model_sha256'] == fixed[label]['model_sha256']
    output['scenes'][label] = dict(legacy_reproduced=True, full_sh3_and_frozen_model_verified=True,
                                 legacy=a, corrected=b)
    print(label, 'verified', flush=True)
output['script_sha256'] = {'baseline': digest(old), 'candidate': digest('scripts/benchmark_depth_reprojection.py')}
save(args.root/'report.json', output)
