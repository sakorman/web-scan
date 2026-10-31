"""Compare frozen CPU scoring implementations on unchanged reconstruction outputs."""

import argparse
import hashlib
import importlib.util
import json
import platform
import shutil
import statistics
import time
from pathlib import Path

import torch


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def score(module, data, job, size):
    # Comparing a saved job with itself holds images and camera geometry fixed.
    # The comparison still decodes and scores both inputs, as the normal CLI does.
    started = time.perf_counter()
    result = module.compare(data, job, job, size)
    return time.perf_counter() - started, result


def compare_results(baseline, candidate):
    errors = []
    assert baseline.keys() == candidate.keys()
    for key in baseline.keys() - {'views', 'mean'}:
        assert baseline[key] == candidate[key], key
    assert len(baseline['views']) == len(candidate['views'])
    for a, b in zip(baseline['views'], candidate['views']):
        assert a.keys() == b.keys()
        for key in a.keys() - {'quick', 'detail'}:
            assert a[key] == b[key], key
        for mode in ['quick', 'detail']:
            assert a[mode]['psnr'] == b[mode]['psnr']
            errors.append(abs(a[mode]['ssim'] - b[mode]['ssim']))
    assert max(errors) == 0, f'SSIM changed: {max(errors)}'
    for mode in ['quick', 'detail']:
        assert baseline['mean'][mode]['psnr'] == candidate['mean'][mode]['psnr']
        assert baseline['mean'][mode]['ssim'] == candidate['mean'][mode]['ssim']
    return max(errors)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--jobs', nargs='+', required=True)
    p.add_argument('--size', type=int, default=1024)
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if not 1 <= args.repeats <= 10:
        p.error('--repeats must be between 1 and 10')
    args.output.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(4)
    modules, identities = {}, {}
    for mode in ['baseline', 'candidate']:
        target = args.output / f'{mode}.py'
        shutil.copy2(getattr(args, mode), target)
        identities[mode] = hashlib.sha256(target.read_bytes()).hexdigest()
        modules[mode] = load(target, f'scoring_{mode}')
    result = {
        'platform': platform.platform(), 'torch': torch.__version__, 'threads': 4,
        'adapters': identities, 'requested_size': args.size, 'repeats': args.repeats,
        'measurement': 'CPU comparison including validation, decoding, resizing and metrics; excludes imports and one warmup per implementation/job; filesystem cache not flushed; no GPU',
        'warmups': [], 'runs': [], 'summary': [],
    }
    for index, job in enumerate(args.jobs):
        outputs = {}
        for mode, module in modules.items():
            seconds, outputs[mode] = score(module, args.data, job, args.size)
            result['warmups'].append({'job': job, 'variant': mode, 'seconds': seconds})
        drift = compare_results(outputs['baseline'], outputs['candidate'])
        for repeat in range(args.repeats):
            order = ['baseline', 'candidate'] if (index + repeat) % 2 == 0 else ['candidate', 'baseline']
            for mode in order:
                seconds, output = score(modules[mode], args.data, job, args.size)
                assert output == outputs[mode], 'Scoring changed between repetitions'
                result['runs'].append({'job': job, 'repeat': repeat, 'variant': mode, 'seconds': seconds})
        summary = {'job': job, 'max_ssim_absolute_delta': drift, 'scores': outputs}
        for mode in modules:
            values = [r['seconds'] for r in result['runs'] if r['job'] == job and r['variant'] == mode]
            summary[mode] = {'median_seconds': statistics.median(values), 'min_seconds': min(values), 'max_seconds': max(values)}
        result['summary'].append(summary)
        (args.output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({k: v for k, v in summary.items() if k != 'scores'}), flush=True)


if __name__ == '__main__':
    main()
