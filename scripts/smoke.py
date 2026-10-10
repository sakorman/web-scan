"""Exercise uploads -> real COLMAP -> training -> artifact downloads against a local server."""

import argparse
import json
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--name", default="纹理立方体 · 照片重建验证")
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=60) as client:

        def post(path, **kwargs):
            r = client.post("/api" + path, **kwargs)
            r.raise_for_status()
            return r.json()

        sid = post("/sessions", json={"name": args.name})["id"]
        for photo in sorted(args.images.glob("*.jpg")):
            post(
                f"/sessions/{sid}/frames",
                files={"file": (photo.name, photo.read_bytes(), "image/jpeg")},
            )
        post(f"/sessions/{sid}/finalize")
        job = post(
            "/jobs",
            json={"session_id": sid, "steps": 120, "max_points": 384, "resolution": 96},
        )
        print("JOB", job["id"], flush=True)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            r = client.get("/api/jobs/" + job["id"])
            r.raise_for_status()
            job = r.json()
            if job["status"] in ("succeeded", "failed", "cancelled"):
                break
            time.sleep(1)
        assert job["status"] == "succeeded", job
        report = job["report"]
        assert report["pose_source"] == "COLMAP" and not report["calibrated_fixture"]
        assert report["registered_images"] >= 3
        for name in [
            "gaussians.ply",
            "preview.splat",
            "report.json",
            "cameras.json",
            "thumbnail.jpg",
        ]:
            r = client.get(f"/api/jobs/{job['id']}/artifacts/{name}")
            r.raise_for_status()
            assert len(r.content) > 0
        print(
            json.dumps(
                {k: v for k, v in report.items() if k != "loss_history"},
                indent=2,
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
