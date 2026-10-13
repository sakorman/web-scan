"""Compare two Brush adapters on identical prepared scenes, with serial GPU runs.

The baseline/candidate files are explicit local snapshots. This benchmark excludes
SfM and measures training plus export; all runs use the same seed and eval split.
"""

import argparse
import hashlib
import importlib.util
import json
import shutil
import time
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_adapter(path):
    spec = importlib.util.spec_from_file_location("bench_adapter", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--jobs", nargs="+", required=True)
    p.add_argument("--steps", type=int, default=6000)
    args = p.parse_args()
    for name in ("repo", "baseline", "candidate", "output"):
        setattr(args, name, getattr(args, name).resolve())
    if args.steps < 500 or args.steps > 30000 or args.steps % 500:
        p.error("--steps must be a multiple of 500 between 500 and 30000")
    args.output.mkdir(parents=True, exist_ok=False)
    binary = args.repo / ".tools/brush/brush_app"
    config = {
        "steps": args.steps,
        "max_points": 150000,
        "resolution": 1024,
        "evaluation_mode": "holdout",
        "backend": "brush",
    }
    results = {
        "config": config,
        "engine_sha256": sha(binary),
        "seed": 42,
        "measurement": "adapter training, evaluation and export; excludes SfM and input copy",
        "runs": [],
    }
    for index, jid in enumerate(args.jobs):
        source = args.repo / ".data/jobs" / jid
        scene = json.loads((source / "sfm/scene.json").read_text())
        # Alternate AB/BA across scenes to reduce systematic order bias.
        for mode in (
            ["baseline", "candidate"] if index % 2 == 0 else ["candidate", "baseline"]
        ):
            work = args.output / f"{index}-{mode}"
            work.mkdir()
            shutil.copytree(source / "sfm/undistorted", work / "sfm/undistorted")
            local_scene = {
                **scene,
                "views": [
                    {
                        **v,
                        "image": str(
                            work / "sfm/undistorted/images" / Path(v["image"]).name
                        ),
                    }
                    for v in scene["views"]
                ],
            }
            input_hash = hashlib.sha256(
                "".join(
                    f"{p.relative_to(work)}:{sha(p)}"
                    for p in sorted((work / "sfm/undistorted").rglob("*"))
                    if p.is_file()
                ).encode()
            ).hexdigest()
            adapter_path = getattr(args, mode)
            adapter = load_adapter(adapter_path)
            adapter.executable = lambda: binary
            events = []

            started = time.monotonic()

            def progress(stage, value, message, events=events, started=started):
                events.append(
                    {
                        "elapsed": round(time.monotonic() - started, 4),
                        "stage": stage,
                        "progress": value,
                        "message": message,
                    }
                )

            print(f"Start {mode} scene {index}: {jid}", flush=True)
            report = adapter.train(
                local_scene, work, work / "artifacts", config, progress
            )
            elapsed = time.monotonic() - started
            exports = list((work / "brush").glob("export_*.ply"))
            stats = {
                "scene_index": index,
                "source_job": jid,
                "variant": mode,
                "seconds": round(elapsed, 4),
                "adapter_sha256": sha(adapter_path),
                "input_sha256": input_hash,
                "gaussians": report["gaussians"],
                "evaluation": report["evaluation"],
                "training_views": report["training_views"],
                "evaluation_views": report["evaluation_views"],
                "exports": len(exports),
                "export_bytes": sum(p.stat().st_size for p in exports),
                "first_progress_seconds": next(
                    (e["elapsed"] for e in events if e["progress"] > 0.42), None
                ),
            }
            (work / "progress.json").write_text(
                json.dumps(events, ensure_ascii=False, indent=2)
            )
            (work / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )
            results["runs"].append(stats)
            (args.output / "results.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2)
            )
            print(json.dumps(stats, ensure_ascii=False), flush=True)
    for index in range(len(args.jobs)):
        a, b = [r for r in results["runs"] if r["scene_index"] == index]
        assert a["input_sha256"] == b["input_sha256"], "inputs differ"
        assert (
            a["training_views"] == b["training_views"]
            and a["evaluation_views"] == b["evaluation_views"]
        ), "view split differs"


if __name__ == "__main__":
    main()
