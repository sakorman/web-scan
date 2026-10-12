"""Compare two identical local training runs and report whether SfM was reused."""

import argparse
import json
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True, help="A ready photo capture ID")
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument("--steps", type=int, default=1000)
    parser.add_argument("--output", type=Path, default=Path(".qa/reuse-benchmark.json"))
    args = parser.parse_args()
    results = []
    with httpx.Client(base_url=args.url, timeout=30) as client:
        for attempt in range(2):
            response = client.post(
                "/api/jobs",
                json={
                    "session_id": args.session,
                    "backend": "brush",
                    "steps": args.steps,
                    "max_points": 50000,
                    "resolution": 512,
                },
            )
            response.raise_for_status()
            job = response.json()
            deadline = time.monotonic() + 600
            print(f"Run {attempt + 1}: {job['id']}", flush=True)
            while time.monotonic() < deadline:
                response = client.get(f"/api/jobs/{job['id']}")
                response.raise_for_status()
                job = response.json()
                if job["status"] in ("succeeded", "failed", "cancelled"):
                    break
                time.sleep(1)
            if job["status"] != "succeeded":
                raise RuntimeError(f"Task did not complete: {job['message']}")
            results.append({"job_id": job["id"], **job["report"]})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    for index, result in enumerate(results, 1):
        print(
            json.dumps(
                {
                    "run": index,
                    "seconds": result["seconds"],
                    "sfm_reused": result["sfm_reused"],
                    "timings": result["timings"],
                }
            )
        )
    if results[0]["sfm_reused"]:
        print(
            "Both runs may be warm. Use a newly imported capture for a cold/warm comparison."
        )
    if not results[1]["sfm_reused"]:
        raise RuntimeError("The second run did not reuse its SfM results")
    if results[0]["normalization"] != results[1]["normalization"]:
        raise RuntimeError("Reused camera geometry differs from the first run")


if __name__ == "__main__":
    main()
