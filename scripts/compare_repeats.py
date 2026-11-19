"""Describe held-out score variation across completed, identical-configuration jobs.

Reads the existing catalog and artifacts; does not create jobs or train models.
The observed range and sample deviation are descriptive, not acceptance thresholds.
"""

import argparse
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import statistics

import torch

from scripts.compare_quality import compare


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def spread(values):
    if len(values) < 2 or not all(math.isfinite(x) for x in values):
        raise ValueError('At least two finite measurements are required')
    return dict(count=len(values), mean=statistics.mean(values),
                sample_std=statistics.stdev(values), min=min(values),
                max=max(values), range=max(values) - min(values))


def load_run(data, db, jid):
    row = db.execute('SELECT status,config,report FROM jobs WHERE id=?', (jid,)).fetchone()
    if row is None or row[0] != 'succeeded':
        raise ValueError(f'Job {jid} is not a completed catalog job')
    config = json.loads(row[1])
    work = data / 'jobs' / jid
    report = json.loads((work / 'artifacts/report.json').read_text())
    if row[2] is None or json.loads(row[2]) != report:
        raise ValueError('Catalog and artifact reports differ')
    if config.get('backend') != 'brush' or config.get('evaluation_mode') != 'holdout':
        raise ValueError('Repeat comparison requires Brush held-out evaluation')
    for key in ('backend', 'steps', 'resolution', 'evaluation_mode'):
        if config.get(key) != report.get(key):
            raise ValueError(f'Report and config disagree: {key}')
    engine = report.get('engine_sha256')
    seed = report.get('training_seed')
    if not isinstance(engine, str) or not re.fullmatch('[0-9a-f]{64}', engine):
        raise ValueError('A recorded engine SHA-256 is required')
    if type(seed) is not int or seed < 0:
        raise ValueError('A recorded training seed is required')
    if not report.get('evaluation_verified'):
        raise ValueError('Verified held-out evaluation is required')
    scene = json.loads((work / 'sfm/scene.json').read_text())
    for view in scene['views']:
        view['image'] = Path(view['image']).name
    folder = work / 'sfm/undistorted'
    files = [{'name': str(p.relative_to(folder)), 'sha256': file_digest(p)}
             for p in sorted(folder.rglob('*')) if p.is_file()]
    if not files:
        raise ValueError('Prepared training inputs are missing')
    identity = dict(config=config, engine_sha256=engine, training_seed=seed,
                    inputs_sha256=digest(files), scene_sha256=digest(scene),
                    cameras_sha256=digest(json.loads((work / 'artifacts/cameras.json').read_text())),
                    training_views=report['training_views'], evaluation_views=report['evaluation_views'])
    return dict(job_id=jid, identity=identity, gaussians=report['gaussians'],
                model_bytes=(work / 'artifacts/gaussians.ply').stat().st_size,
                timings=report.get('timings'), task_seconds=report.get('seconds'))


def summarize(data, jobs, size=512):
    data = Path(data).resolve()
    if len(jobs) < 2 or len(set(jobs)) != len(jobs):
        raise ValueError('Provide at least two distinct job IDs')
    if any(not re.fullmatch('[0-9a-f]{32}', jid) for jid in jobs):
        raise ValueError('Invalid job ID')
    uri = (data / 'state.sqlite3').as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as db:
        runs = [load_run(data, db, jid) for jid in jobs]
    first = runs[0]['identity']
    for run in runs[1:]:
        differing = [key for key in first if run['identity'][key] != first[key]]
        if differing:
            raise ValueError('Repeat conditions differ: ' + ', '.join(differing))
    views = None
    for run in runs:
        # Reuse common-camera, reference hash, render completeness and size checks.
        paired = compare(data, jobs[0], run['job_id'], size)
        measured = [dict(view=v['view'], dimensions=v['scoring_dimensions'], **v['detail'])
                    for v in paired['views']]
        keys = [(v['view'], v['dimensions']) for v in measured]
        if views is None:
            views = keys
        elif views != keys:
            raise ValueError('Scoring grids or evaluation views differ')
        run['scores'] = paired['mean']['detail']
        run['views'] = measured
        del run['identity']
    return dict(identity=first, requested_size=size, runs=runs,
                score_summary={m: spread([r['scores'][m] for r in runs]) for m in ('psnr', 'ssim')},
                view_summary=[dict(view=name, dimensions=dimensions,
                    **{m: spread([r['views'][i][m] for r in runs]) for m in ('psnr', 'ssim')})
                    for i, (name, dimensions) in enumerate(views)],
                gaussians=spread([r['gaussians'] for r in runs]),
                model_bytes=spread([r['model_bytes'] for r in runs]),
                interpretation='Observed same-seed variation only; not a confidence interval, causal test or quality acceptance threshold.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', type=Path, default=Path('.data'))
    p.add_argument('--jobs', nargs='+', required=True)
    p.add_argument('--size', type=int, default=512)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    torch.set_num_threads(4)
    result = summarize(args.data, args.jobs, args.size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result['score_summary'], indent=2))


if __name__ == '__main__':
    main()
