"""Serial reciprocal-depth experiment using frozen real-photo reconstructions."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, required=True)
args = parser.parse_args()
root = args.root
root.mkdir(parents=True, exist_ok=False)
shutil.copyfile('docs/benchmarks/gmdepth-cycle-config.json', root/'config.json')
baseline = Path('.data/benchmarks/gmdepth-20261107')
sensor = Path('.data/benchmarks/depth-overlap-20261103')
started = time.monotonic()


def run(script, *arguments):
    subprocess.run([sys.executable, script, *map(str, arguments)], check=True)


for scene in ('pipes', 'office', 'tum'):
    pairs = baseline/scene/'pairs.json'
    reverse = root/(scene+'-reverse')
    audit = root/(scene+'-audit')
    run('scripts/benchmark_gmdepth_cycle.py', 'prepare', '--pairs', pairs, '--output', reverse)
    for row in json.loads((reverse/'index.json').read_text())['records']:
        directory = reverse/row['directory']
        run('scripts/benchmark_gmdepth.py', 'infer', '--pairs', directory/'pairs.json',
            '--repo', '.data/research/unimatch', '--weights', '.data/research/gmdepth-scale1-scannet-d3d1efb5.pth',
            '--device', 'mps', '--output', directory/'inference')
    extra = ['--raw-anchors', sensor/'sensor/anchors.json'] if scene == 'tum' else []
    run('scripts/benchmark_gmdepth_cycle.py', 'audit', '--pairs', pairs, '--predictions', baseline/(scene+'-mps'),
        '--reverse', reverse, '--config', root/'config.json', '--output', audit, *extra)
run('scripts/benchmark_gmdepth_cycle.py', 'score', '--audit', root/'tum-audit',
    '--baseline', baseline/'tum-final', '--sensor-root', sensor, '--output', root/'tum-score')
(root/'complete.json').write_text(json.dumps(dict(status='complete', seconds=time.monotonic()-started), indent=2)+'\n')
