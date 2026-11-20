"""Decompose fixed-view residuals without changing renders or quality scores."""
import argparse
import importlib.util
import json
from pathlib import Path
import time

import torch

from scripts.compare_quality import compare
from scripts.compare_repeats import summarize


def previous_fields(result):
    result = json.loads(json.dumps(result))
    for view in result['views']:
        for mode in ('quick', 'detail'):
            for tile in view[mode]['error_tiles']:
                for key in ('mean_rgb_error', 'bias_mse', 'centered_mse'):
                    tile.pop(key, None)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--new-runs', type=Path, required=True)
    p.add_argument('--baseline-module', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    plan = json.loads(args.plan.read_text())
    fresh = json.loads(args.new_runs.read_text())
    spec = importlib.util.spec_from_file_location('baseline_compare', args.baseline_module)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    torch.set_num_threads(4)
    result = {'plan': plan, 'scenes': {}, 'new_runs': fresh}
    for scene, jobs in plan['historical_jobs'].items():
        jobs = jobs + [r['job_id'] for r in fresh if r['scene'] == scene]
        checked = summarize(Path('.data'), jobs, plan['size'])
        rows = []
        for jid in jobs:
            before = old.compare(Path('.data'), jid, jid, plan['size'], error_grid=plan['grid'])
            start = time.perf_counter()
            after = compare(Path('.data'), jid, jid, plan['size'], error_grid=plan['grid'])
            whole = compare(Path('.data'), jid, jid, plan['size'], error_grid=1)
            seconds = time.perf_counter() - start
            assert previous_fields(after) == before, 'Existing scores or tile MSE changed'
            views = []
            for v, w in zip(after['views'], whole['views'], strict=True):
                tiles = v['detail']['error_tiles']
                weights = [(t['xyxy'][2]-t['xyxy'][0])*(t['xyxy'][3]-t['xyxy'][1]) for t in tiles]
                bias = sum(a*t['bias_mse'] for a,t in zip(weights,tiles))/sum(weights)
                centered = sum(a*t['centered_mse'] for a,t in zip(weights,tiles))/sum(weights)
                full = w['detail']['error_tiles'][0]
                assert abs(full['mse']-bias-centered)<1e-12
                views.append(dict(view=v['view'],dimensions=v['scoring_dimensions'],
                    psnr=v['detail']['psnr'],ssim=v['detail']['ssim'],whole=full,tiles=tiles,
                    whole_bias_fraction=full['bias_mse']/full['mse'] if full['mse'] else 0,
                    tile_bias_fraction=bias/full['mse'] if full['mse'] else 0,
                    tile_weighted_bias_mse=bias,tile_weighted_centered_mse=centered))
            rows.append(dict(job_id=jid,unchanged_scores=True,analysis_seconds=seconds,
                             scores=after['mean']['detail'],views=views))
        result['scenes'][scene]={'verified_conditions':checked['identity'],'jobs':rows}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    for scene,s in result['scenes'].items():
        for r in s['jobs']:
            v=next(v for v in r['views'] if v['view'].startswith('0008-'))
            print(scene,r['job_id'][:8],r['scores'], 'view8',v['psnr'],
                  'global/tile fraction', v['whole_bias_fraction'],v['tile_bias_fraction'],
                  'mse',v['whole']['mse'],'bias',v['tile_weighted_bias_mse'],
                  'centered',v['tile_weighted_centered_mse'])


if __name__ == '__main__':
    main()
