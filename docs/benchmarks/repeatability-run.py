"""Run three serial, unchanged Brush jobs per ETH3D scene through the local API."""
import argparse
import json
from pathlib import Path
import time

import httpx

SOURCES = {'pipes': '25db943c3f0d4e9193cd6086080815d5',
           'office': '9c211f32cd6a48aa85beba8379febd48'}
ORDER = ['pipes', 'office', 'office', 'pipes', 'pipes', 'office']
CONFIG = dict(backend='brush', steps=3000, max_points=150000, resolution=512,
              camera_mode='shared', evaluation_mode='holdout')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--url', default='http://127.0.0.1:8787')
    args = p.parse_args()
    if args.output.exists():
        p.error('Output already exists; preserve it and choose a new path')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with httpx.Client(base_url=args.url, timeout=30) as client:
        for scene in ORDER:
            response = client.get(f'/api/jobs/{SOURCES[scene]}')
            response.raise_for_status()
            source = response.json()
            assert source['config'] == CONFIG
            # Avoid treating an already running job as a new repeat.
            session = client.get(f"/api/sessions/{source['session_id']}")
            session.raise_for_status()
            if any(j['status'] in ('queued', 'running') for j in session.json()['jobs']):
                raise RuntimeError('Source session has an active reconstruction')
            start = time.monotonic()
            response = client.post('/api/jobs', json=dict(session_id=source['session_id'], **CONFIG))
            response.raise_for_status()
            job = response.json()
            if job['id'] in [r['job_id'] for r in results]:
                raise RuntimeError('API returned a previous job')
            item = dict(scene=scene, repeat=1 + sum(r['scene'] == scene for r in results),
                        job_id=job['id'], source_job=SOURCES[scene], config=CONFIG, checks=[])
            results.append(item)
            args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2))
            print(f"Start {scene} repeat {item['repeat']}: {job['id']}", flush=True)
            while time.monotonic() - start < 600:
                health = client.get('/api/health'); health.raise_for_status()
                history = client.get('/api/sessions'); history.raise_for_status()
                item['checks'].append(dict(health=health.status_code, history=history.status_code,
                                           worker_running=health.json()['worker_running']))
                response = client.get(f"/api/jobs/{job['id']}"); response.raise_for_status()
                job = response.json()
                if job['status'] not in ('queued', 'running'):
                    break
                time.sleep(1)
            item.update(status=job['status'], report=job['report'],
                        request_to_observed_completion_seconds=time.monotonic() - start)
            args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2))
            if job['status'] != 'succeeded':
                raise RuntimeError(f"Task {job['id']}: {job['message']}")
            print(f"Complete: {job['report']['seconds']}s, {job['report']['gaussians']} splats", flush=True)


if __name__ == '__main__':
    main()
