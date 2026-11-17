"""Serial SH learning-rate experiment using frozen, already registered photographs.

Run from the repository root:
PYTHONPATH=. .venv/bin/python docs/benchmarks/sh-slow-learning.py --root OUTPUT
Only the higher-order SH learning-rate divisor differs (20 vs 40).
"""
import argparse
import json
import shutil
import time
from pathlib import Path

from scripts.benchmark_exports import load_adapter, sha, write_comparison_artifacts

SOURCES = {
    'pipes': '9b99bf8268944f1bba8ede3c2cd4b240',
    'office': '9b61678793164e06b9c37a617bfd46dd',
}
CONFIG = dict(backend='brush', steps=3000, max_points=150000, resolution=512,
              camera_mode='shared', evaluation_mode='holdout')
ORDER = [(s, v, r) for v, r, names in (
    ('baseline', 1, ('pipes', 'office')),
    ('candidate', 1, ('pipes', 'office')),
    ('candidate', 2, ('office', 'pipes')),
    ('baseline', 2, ('office', 'pipes')),
) for s in names]


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--steps', type=int, default=3000,
                        help='Total steps for both variants; resolution remains 512')
    args = parser.parse_args()
    if not 500 <= args.steps <= 30000 or args.steps % 500:
        parser.error('--steps must be a multiple of 500 between 500 and 30000')
    config = {**CONFIG, 'steps': args.steps}
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / 'adapters').mkdir()
    baseline = root / 'adapters/baseline.py'
    shutil.copy2('webscan/brush.py', baseline)
    binary = Path('.tools/brush/brush_app').resolve()
    plan = dict(config=config, sources=SOURCES, order=ORDER, seed=42,
                engine_sha256=sha(binary), baseline_adapter_sha256=sha(baseline),
                baseline_sh_divisor=20, candidate_sh_divisor=40,
                threshold=dict(min_psnr_delta=-0.2, min_ssim_delta=-0.005),
                required='Both scenes pass thresholds; target pipes view 8 tile 12 improves in both repeats',
                cache='Frozen SfM reused. Metal disk cache not flushed; fresh Brush process per run.')
    save(root / 'plan.json', plan)
    results = []
    for scene_name, variant, repeat in ORDER:
        adapter_path = root / 'adapters' / f'{variant}.py'
        if not adapter_path.exists():
            # Create the candidate only after both first baselines have completed.
            source = baseline.read_text()
            anchor = '    log_path = work / "brush.log"\n'
            assert source.count(anchor) == 1
            adapter_path.write_text(source.replace(anchor,
                '    args += ["--lr-coeffs-sh-scale", "40"]\n' + anchor))
        work = root / 'jobs' / f'{scene_name}-{variant}-{repeat}'
        work.mkdir(parents=True, exist_ok=False)
        start = time.monotonic()
        source = Path('.data/jobs') / SOURCES[scene_name]
        shutil.copytree(source / 'sfm', work / 'sfm')
        scene = json.loads((work / 'sfm/scene.json').read_text())
        for view in scene['views']:
            view['image'] = str(work / 'sfm/undistorted/images' / view['image_name'])
        identity = [{'name': str(p.relative_to(work / 'sfm/undistorted')), 'sha256': sha(p)}
                    for p in sorted((work / 'sfm/undistorted').rglob('*')) if p.is_file()]
        save(work / 'inputs.json', identity)
        adapter = load_adapter(adapter_path)
        adapter.executable = lambda: binary
        print(f'Start {work.name}', flush=True)
        training_start = time.monotonic()
        report = adapter.train(scene, work, work / 'artifacts', config, lambda *_: None)
        training_seconds = time.monotonic() - training_start
        write_comparison_artifacts(work, scene, report)
        _, points = adapter.read_ply(work / 'artifacts/gaussians.ply', expected_sh_degree=3)
        assert len(points) == report['gaussians']
        record = dict(name=work.name, scene=scene_name, variant=variant, repeat=repeat,
                      source_job=SOURCES[scene_name], adapter_sha256=sha(adapter_path),
                      inputs_sha256=sha(work / 'inputs.json'),
                      cameras_sha256=sha(work / 'artifacts/cameras.json'),
                      sfm_reused=True, sfm_seconds=0,
                      training_export_seconds=training_seconds,
                      reuse_pipeline_seconds=time.monotonic() - start,
                      input_images=scene['input_images'], registered_images=scene['registered_images'],
                      unregistered_images=scene['unregistered_images'],
                      original_camera=scene['views'][0]['original_camera'],
                      model_bytes=(work / 'artifacts/gaussians.ply').stat().st_size,
                      model_sha256=sha(work / 'artifacts/gaussians.ply'), report=report)
        save(work / 'result.json', record)
        results.append(record)
        save(root / 'results.json', results)
        print(f'Complete {work.name}: {training_seconds:.3f}s, {len(points)} splats', flush=True)
    for name in SOURCES:
        group = [r for r in results if r['scene'] == name]
        assert len({r['inputs_sha256'] for r in group}) == 1
        assert len({r['cameras_sha256'] for r in group}) == 1
    save(root / 'complete.json', {'runs': len(results), 'identities_match': True})


if __name__ == '__main__':
    main()
