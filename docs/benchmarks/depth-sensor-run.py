"""Frozen RGB-only TUM reconstruction for the sensor-depth diagnostic."""
import argparse
import json
import shutil
import time
from pathlib import Path

from scripts.benchmark_depth_prior import digest, save
from scripts.benchmark_exports import write_comparison_artifacts
from webscan.brush import train
from webscan.reconstruction import estimate_scene

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, default=Path('.data/benchmarks/depth-sensor-20261101'))
root = parser.parse_args().root
config = json.loads((root / 'config.json').read_text())
work = root / 'jobs/tum-baseline'
work.mkdir(parents=True, exist_ok=False)
(work / 'input').mkdir()
result = {'status': 'running', 'inputs': [], 'config': config['training']}
started = time.monotonic()
try:
    source = Path('.samples/tum-freiburg1_desk')
    manifest = json.loads((source / 'source.json').read_text())
    for i, row in enumerate(manifest['files']):
        path = source / row['name']
        assert digest(path) == row['sha256']
        name = f'{i:04d}-{row["name"]}'
        shutil.copy2(path, work / 'input' / name)
        result['inputs'].append({**row, 'name': name, 'timestamp': Path(row['name']).stem})
    start = time.monotonic()
    scene = estimate_scene(work / 'input', work / 'sfm', lambda *_: None, camera_mode='shared')
    result['sfm_seconds'] = time.monotonic() - start
    scene['points'] = scene['points'].tolist()
    scene['colors'] = scene['colors'].tolist()
    write_comparison_artifacts(work, scene, {})
    result['sfm'] = {k: scene[k] for k in ('input_images', 'registered_images', 'sparse_points', 'reprojection_error', 'unregistered_images', 'camera_model', 'camera_mode_effective')}
    result['sfm']['original_cameras'] = list({json.dumps(v['original_camera'], sort_keys=True): v['original_camera'] for v in scene['views']}.values())
    result['sfm']['undistorted_sizes'] = sorted({(v['width'], v['height']) for v in scene['views']})
    start = time.monotonic()
    report = train(scene, work, work / 'artifacts', config['training'], lambda *_: None)
    result['training_export_seconds'] = time.monotonic() - start
    write_comparison_artifacts(work, scene, report)
    result['report'] = report
    result['model_bytes'] = (work / 'artifacts/gaussians.ply').stat().st_size
    for row in result['inputs']:
        assert digest(work / 'input' / row['name']) == row['sha256']
    result.update(status='complete', inputs_unchanged=True)
except BaseException as error:
    result.update(status='failed', error=f'{type(error).__name__}: {error}')
    raise
finally:
    result['end_to_end_seconds'] = time.monotonic() - started
    save(work / 'result.json', result)
    print(result['status'], result['end_to_end_seconds'], flush=True)
