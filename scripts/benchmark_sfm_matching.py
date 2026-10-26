"""Compare exhaustive and sequential COLMAP matching on one ordered capture.

This measures only feature extraction, matching, mapping and undistortion. It
does not train a Gaussian model, so output directories remain independent from
application jobs and historical models.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path

from webscan.brush import train as train_brush
from webscan.reconstruction import estimate_scene


def digest_inputs(images: Path) -> list[dict]:
    records = []
    for image in sorted(images.glob("*.jpg")):
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        records.append({"name": image.name, "bytes": image.stat().st_size, "sha256": digest})
    if len(records) < 3:
        raise ValueError("至少需要 3 张 JPEG 图片")
    return records


def train_scene(scene, work: Path, *, steps: int, resolution: int):
    events = []
    started = time.monotonic()
    report = train_brush(
        scene,
        work,
        work / "artifacts",
        {
            "backend": "brush",
            "steps": steps,
            "max_points": 150000,
            "resolution": resolution,
            "evaluation_mode": "holdout",
        },
        lambda stage, value, message: events.append(
            {
                "stage": stage,
                "progress": value,
                "message": message,
                "elapsed": round(time.monotonic() - started, 4),
            }
        ),
    )
    (work / "training-progress.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2)
    )
    return {
        "seconds": round(time.monotonic() - started, 4),
        "gaussians": report["gaussians"],
        "evaluation": report["evaluation"],
        "training_views": report["training_views"],
        "evaluation_views": report["evaluation_views"],
        "engine_sha256": report["engine_sha256"],
        "model_bytes": (work / "artifacts" / "gaussians.ply").stat().st_size,
    }


def run(
    images: Path,
    output: Path,
    *,
    mode: str,
    camera_mode: str,
    overlap: int,
    training_steps: int,
    resolution: int,
):
    work = output / mode
    sfm = work / "sfm"
    started = time.monotonic()
    scene = estimate_scene(
        images,
        sfm,
        lambda *_: None,
        camera_mode=camera_mode,
        matching_mode=mode,
        sequential_overlap=overlap,
    )
    serialized = json.dumps(
        {
            **scene,
            "points": scene["points"].tolist(),
            "colors": scene["colors"].tolist(),
        },
        ensure_ascii=False,
        indent=2,
    )
    (sfm / "scene.json").write_text(serialized)
    (work / "scene.json").write_text(
        serialized
    )
    result = {
        "mode": mode,
        "seconds": round(time.monotonic() - started, 4),
        "camera_mode_effective": scene.get("camera_mode_effective", camera_mode),
        "camera_mode_retry": scene.get("camera_mode_retry"),
        "registered_images": scene["registered_images"],
        "input_images": scene["input_images"],
        "sparse_points": scene["sparse_points"],
        "reprojection_error": scene["reprojection_error"],
        "unregistered_images": scene["unregistered_images"],
        "matching": scene["matching"],
    }
    if training_steps:
        result["training"] = train_scene(
            scene, work, steps=training_steps, resolution=resolution
        )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--camera-mode", choices=("auto", "shared"), default="auto")
    parser.add_argument(
        "--matching-modes",
        nargs="+",
        choices=("exhaustive", "sequential"),
        default=("exhaustive", "sequential"),
        help="matching strategies to run in order",
    )
    parser.add_argument("--overlap", type=int, default=10)
    parser.add_argument(
        "--training-steps",
        type=int,
        default=0,
        help="optional Brush screening run; 0 skips Gaussian training",
    )
    parser.add_argument("--resolution", type=int, default=512)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("输出目录已存在；请使用新的实验目录")
    if args.training_steps and (
        args.training_steps < 500 or args.training_steps % 500
    ):
        raise ValueError("训练步数需为 500 的倍数，至少 500")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.mkdir()
    try:
        inputs = digest_inputs(args.images)
        result = {
            "measurement": (
                "SfM feature extraction, matching, mapping and undistortion"
                + (
                    "; each run also includes Brush training, holdout evaluation and export"
                    if args.training_steps
                    else "; excludes Gaussian training"
                )
            ),
            "camera_mode": args.camera_mode,
            "training": (
                {
                    "steps": args.training_steps,
                    "resolution": args.resolution,
                    "max_points": 150000,
                    "evaluation_mode": "holdout",
                }
                if args.training_steps
                else None
            ),
            "inputs": inputs,
            "runs": [
                run(
                    args.images,
                    args.output,
                    mode=mode,
                    camera_mode=args.camera_mode,
                    overlap=args.overlap,
                    training_steps=args.training_steps,
                    resolution=args.resolution,
                )
                for mode in args.matching_modes
            ],
        }
        (args.output / "summary.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        print(json.dumps(result, ensure_ascii=False))
    except Exception:
        shutil.rmtree(args.output)
        raise


if __name__ == "__main__":
    main()
