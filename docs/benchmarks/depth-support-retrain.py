"""Independent RGB training control using the frozen TUM SfM scene."""
import json
import shutil
import time
from pathlib import Path

from scripts.benchmark_depth_prior import digest, save
from scripts.benchmark_exports import evaluation_reference_identity, write_comparison_artifacts
from webscan.brush import read_ply, train

root = Path('.data/benchmarks/depth-support-20261102')
source = Path('.data/benchmarks/depth-sensor-20261101/jobs/tum-baseline')
original = json.loads((source / 'result.json').read_text())
scene = json.loads((source / 'sfm/scene.json').read_text())
config = original['config']
work = root / 'jobs/tum-control'
work.mkdir(parents=True, exist_ok=False)
save(root / 'training-config.json', {'training': config, 'source_scene_sha256': digest(source/'sfm/scene.json'),
     'source_result_sha256': digest(source/'result.json'), 'scope': 'cached SfM; unchanged RGB training control'})
result = {'status': 'running', 'config': config, 'inputs': original['inputs'], 'sfm': original['sfm'],
          'sfm_reused': True, 'sfm_seconds': 0., 'source_sfm_seconds': original['sfm_seconds']}
start_all = time.monotonic()
try:
    shutil.copytree(source/'input', work/'input')
    shutil.copytree(source/'sfm/undistorted', work/'sfm/undistorted')
    for view in scene['views']:
        view['image'] = str((work/'sfm/undistorted/images'/Path(view['image']).name).resolve())
    start = time.monotonic()
    report = train(scene, work, work/'artifacts', config, lambda *_: None)
    result['training_export_seconds'] = time.monotonic()-start
    write_comparison_artifacts(work, scene, report)
    assert evaluation_reference_identity(report) == evaluation_reference_identity(original['report'])
    assert digest(work/'artifacts/cameras.json') == digest(source/'artifacts/cameras.json')
    for row in result['inputs']:
        assert digest(source/'input'/row['name']) == digest(work/'input'/row['name']) == row['sha256']
    model = work/'artifacts/gaussians.ply'
    header, data = read_ply(model, expected_sh_degree=3)
    assert len(data) == report['gaussians']
    result.update(report=report, model_bytes=model.stat().st_size, model_sha256=digest(model),
                  status='complete', inputs_unchanged=True, cameras_unchanged=True, evaluation_identity_verified=True)
except BaseException as error:
    result.update(status='failed', error=f'{type(error).__name__}: {error}')
    raise
finally:
    result['cached_task_seconds'] = time.monotonic()-start_all
    save(work/'result.json', result)
    print(result['status'], result['cached_task_seconds'], flush=True)
