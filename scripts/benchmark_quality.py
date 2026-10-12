"""Compare quick and detailed training on identical cached poses and eval views."""

import argparse
import json
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", nargs="+", required=True)
    parser.add_argument("--camera-mode", choices=["auto", "shared"], default="shared")
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument(
        "--output", type=Path, default=Path(".qa/quality-benchmark.json")
    )
    args = parser.parse_args()
    results = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=args.url, timeout=60) as client:
        for sid in args.sessions:
            previous = None
            for label, steps, resolution in [
                ("quick", 3000, 512),
                ("detail", 10000, 1024),
            ]:
                config = {
                    "session_id": sid,
                    "backend": "brush",
                    "steps": steps,
                    "resolution": resolution,
                    "max_points": 300000,
                    "camera_mode": args.camera_mode,
                    "evaluation_mode": "holdout",
                }
                response = client.post("/api/jobs", json=config)
                response.raise_for_status()
                job = response.json()
                print(f"{sid} {label}: {job['id']}", flush=True)
                deadline = time.monotonic() + 3600
                while (
                    job["status"] in ("queued", "running")
                    and time.monotonic() < deadline
                ):
                    time.sleep(3)
                    response = client.get(f"/api/jobs/{job['id']}")
                    response.raise_for_status()
                    job = response.json()
                if job["status"] != "succeeded":
                    raise RuntimeError(job["message"])
                result = {
                    "session_id": sid,
                    "preset": label,
                    "job_id": job["id"],
                    "config": config,
                    "report": job["report"],
                }
                results.append(result)
                args.output.write_text(
                    json.dumps(results, ensure_ascii=False, indent=2)
                )
                print(json.dumps(result, ensure_ascii=False), flush=True)
                if previous:
                    assert job["report"]["sfm_reused"], (
                        "comparison must reuse identical poses"
                    )
                    assert previous["normalization"] == job["report"]["normalization"]
                    assert (
                        previous["registered_images"]
                        == job["report"]["registered_images"]
                    )
                previous = job["report"]


if __name__ == "__main__":
    main()
