"""Repeat a comparison of two Brush adapters on identical prepared scenes, with serial GPU runs.

The baseline/candidate files are explicit local snapshots. This benchmark excludes
SfM and measures training plus export; all runs use the same seed and eval split.
"""

import argparse
import hashlib
import importlib.util
import json
import math
import shutil
import statistics
import time
from pathlib import Path

import numpy as np


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_adapter(path):
    spec = importlib.util.spec_from_file_location("bench_adapter", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def freeze_adapters(output, sources):
    folder = output / "adapters"
    folder.mkdir()
    frozen = {}
    for label, source in sources.items():
        target = folder / f"{label}.py"
        shutil.copy2(source, target)
        frozen[label] = target
    return frozen


def write_comparison_artifacts(work, scene, report):
    """Write the task artifacts required by the image comparison utility."""
    sfm = work / "sfm"
    artifacts = work / "artifacts"
    sfm.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    (sfm / "scene.json").write_text(json.dumps(scene, ensure_ascii=False, indent=2))
    matrix = np.asarray(scene["views"][0]["view"], dtype=np.float64)
    position = (-matrix[:3, :3].T @ matrix[:3, 3]).tolist()
    (artifacts / "cameras.json").write_text(
        json.dumps(
            {
                "position": position,
                "target": [0, 0, 0],
                "coordinate_system": "COLMAP right-down-forward",
                "views": [
                    {key: value for key, value in view.items() if key != "image"}
                    for view in scene["views"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    (artifacts / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )


def run_order(scene, repeat):
    return (
        ["baseline", "candidate"]
        if (scene + repeat) % 2 == 0
        else ["candidate", "baseline"]
    )


def summarize(runs, repeats):
    result = []
    for scene in sorted({run["scene_index"] for run in runs}):
        group = [run for run in runs if run["scene_index"] == scene]
        if (
            len(group) != 2 * repeats
            or len({(r["repeat"], r["variant"]) for r in group}) != 2 * repeats
        ):
            raise ValueError("Incomplete or duplicate comparison runs")
        if len({run["input_sha256"] for run in group}) != 1:
            raise ValueError("Comparison inputs differ")
        if (
            len({(run["training_views"], run["evaluation_views"]) for run in group})
            != 1
        ):
            raise ValueError("Comparison view splits differ")
        row = {"scene_index": scene, "runs_per_variant": repeats}
        for mode in ("baseline", "candidate"):
            selected = [run for run in group if run["variant"] == mode]
            if {r["repeat"] for r in selected} != set(range(repeats)):
                raise ValueError("Missing comparison repetition")
            stats = {}
            for key in ("seconds", "gaussians", "psnr", "ssim"):
                values = [
                    run["evaluation"][key] if key in ("psnr", "ssim") else run[key]
                    for run in selected
                ]
                if not all(
                    isinstance(value, (int, float)) and math.isfinite(value)
                    for value in values
                ):
                    raise ValueError("Comparison contains invalid metrics")
                stats[key] = {
                    "mean": statistics.mean(values),
                    "min": min(values),
                    "max": max(values),
                }
            row[mode] = stats
        result.append(row)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--jobs", nargs="+", required=True)
    p.add_argument("--steps", type=int, default=6000)
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--resolution", type=int, choices=[512, 1024, 1600], default=1024)
    args = p.parse_args()
    for name in ("repo", "baseline", "candidate", "output"):
        setattr(args, name, getattr(args, name).resolve())
    if args.steps < 500 or args.steps > 30000 or args.steps % 500:
        p.error("--steps must be a multiple of 500 between 500 and 30000")
    if not 1 <= args.repeats <= 5:
        p.error("--repeats must be between 1 and 5")
    args.output.mkdir(parents=True, exist_ok=False)
    snapshots = freeze_adapters(
        args.output, {"baseline": args.baseline, "candidate": args.candidate}
    )
    binary = args.repo / ".tools/brush/brush_app"
    config = {
        "steps": args.steps,
        "max_points": 150000,
        "resolution": args.resolution,
        "evaluation_mode": "holdout",
        "backend": "brush",
    }
    results = {
        "config": config,
        "engine_sha256": sha(binary),
        "seed": 42,
        "repeats": args.repeats,
        "measurement": "adapter training, evaluation and export; excludes SfM and input copy",
        "runs": [],
    }
    for index, jid in enumerate(args.jobs):
        source = args.repo / ".data/jobs" / jid
        scene = json.loads((source / "sfm/scene.json").read_text())
        for repeat in range(args.repeats):
            for mode in run_order(index, repeat):
                work = args.output / (
                    f"{index}-{mode}"
                    if args.repeats == 1
                    else f"{index}-{repeat}-{mode}"
                )
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
                adapter_path = snapshots[mode]
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

                print(f"Start {mode} scene {index}, repeat {repeat}: {jid}", flush=True)
                report = adapter.train(
                    local_scene, work, work / "artifacts", config, progress
                )
                write_comparison_artifacts(work, local_scene, report)
                elapsed = time.monotonic() - started
                exports = list((work / "brush").glob("export_*.ply"))
                stats = {
                    "scene_index": index,
                    "repeat": repeat,
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
    results["summary"] = summarize(results["runs"], args.repeats)
    (args.output / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2)
    )
    print(json.dumps(results["summary"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
