"""Verify sparse training colors and fixed-view scores on cached and new reconstructions."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--runs', type=Path, required=True)
    p.add_argument('--work-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=False)
    plan = json.loads(args.plan.read_text())
    runs = json.loads(args.runs.read_text())
    audits = {}
    for run in runs:
        scene = run['scene']
        audits[scene] = []
        for jid in [plan['sources'][scene], run['job_id']]:
            out = args.work_dir/f'{jid}.json'
            subprocess.run([sys.executable, 'scripts/benchmark_track_color.py', '--job', jid,
                            '--focus-index', str(plan['focus_index']), '--duplicate-policy', 'reject-track',
                            '--output', str(out)], check=True)
            audits[scene].append(json.loads(out.read_text()))
        a, b = [{k:v for k,v in d.items() if k not in ('job','analysis_seconds')} for d in audits[scene]]
        assert a == b, 'Cached camera, input or color statistics changed'
    # Separate process from pycolmap to avoid loading two OpenMP runtimes.
    import torch
    from scripts.compare_repeats import summarize
    from webscan.brush import read_ply
    torch.set_num_threads(4)
    result = dict(plan=plan, scenes={})
    for run in runs:
        scene = run['scene']
        a, b = audits[scene]
        summary = summarize(Path('.data'), [a['job'], b['job']], 512)
        for jid in (a['job'], b['job']):
            read_ply(Path('.data/jobs')/jid/'artifacts/gaussians.ply', expected_sh_degree=3)
        w, h = a['focus_dimensions']
        coverage = [[0]*4 for _ in range(4)]
        for pt in a['focus_points']:
            x, y = pt['xy']
            coverage[min(3,int(y/h*4))][min(3,int(x/w*4))] += 1
        result['scenes'][scene] = dict(unchanged_input_audit=True, scores=summary,
            input_audit={k:v for k,v in a.items() if k != 'focus_points'},
            focus_grid_4x4=coverage, focus_tracks=len(a['focus_points']),
            new_audit_seconds=b['analysis_seconds'],
            service_checks=len(run['checks']),
            service_checks_ok=all(c['health']==200 and c['history']==200 and c['worker_running'] for c in run['checks']),
            request_to_observed_completion_seconds=run['request_to_observed_completion_seconds'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    for scene, entry in result['scenes'].items():
        print(scene, 'coverage', entry['focus_grid_4x4'])
        for r in entry['scores']['runs']:
            print(r['job_id'], r['scores'], r['gaussians'], r['model_bytes'], r['timings'])


if __name__ == '__main__':
    main()
