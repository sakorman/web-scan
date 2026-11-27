"""Evaluate two RGB-D trackers directly on an official Freiburg1 archive."""
import argparse
import io
import json
import tarfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

from scripts.benchmark_rgbd_odometry import frame_identity, odometry, solve_relative, trajectory_score
from scripts.benchmark_rgbd_training import prepare
from scripts.benchmark_sensor_depth import associate
from scripts.benchmark_sfm_matching import train_scene
from scripts.prepare_tum import MAX_IMAGE_BYTES
from scripts.rgbd_pose_graph import refine


def read_sequence(archive, prefix, images, count):
    """Sample by RGB timestamps and unpack only the selected registered sensor images."""
    with tarfile.open(archive) as bundle:
        rgb = np.loadtxt(bundle.extractfile(prefix + "rgb.txt"), dtype=str, ndmin=2)
        depth = np.loadtxt(bundle.extractfile(prefix + "depth.txt"), dtype=str, ndmin=2)
    if count < 8 or len(rgb) < count or rgb.shape[1] != 2 or depth.shape[1] != 2:
        raise ValueError("Invalid frame count or timestamp index")
    stamps = rgb[:, 0].astype(float)
    if not np.isfinite(stamps).all() or np.any(np.diff(stamps) <= 0):
        raise ValueError("Expected strictly increasing RGB timestamps")
    indices = [round(i * (len(rgb) - 1) / (count - 1)) for i in range(count)]
    chosen = rgb[indices]
    pairs = associate(stamps[indices], depth[:, 0].astype(float), .02)
    rgbs = {prefix + name: i for i, (_, name) in enumerate(chosen)}
    depths = {prefix + depth[j, 1]: i for i, (j, _) in pairs.items()}
    images.mkdir()
    frames = [(float(stamp), images / f"{i:04d}-{float(stamp):.6f}.jpg")
              for i, (stamp, _) in enumerate(chosen)]
    measured = {}
    with tarfile.open(archive, "r|gz") as bundle:
        for member in bundle:
            if member.name not in rgbs and member.name not in depths:
                continue
            if not member.isfile() or not 0 < member.size <= MAX_IMAGE_BYTES:
                raise ValueError("Invalid sensor image")
            with Image.open(io.BytesIO(bundle.extractfile(member).read())) as image:
                if image.format != "PNG" or image.size != (640, 480):
                    raise ValueError("Expected native 640 × 480 PNG images")
                if member.name in rgbs:
                    _, path = frames[rgbs.pop(member.name)]
                    # Match prepare_tum (quality 95) followed by normal photo import (94).
                    prepared = io.BytesIO()
                    image.convert("RGB").save(prepared, format="JPEG", quality=95)
                    prepared.seek(0)
                    with Image.open(prepared) as sample:
                        sample.convert("RGB").save(path, quality=94)
                else:
                    depth_image = np.array(image)
                    if depth_image.dtype != np.uint16 or depth_image.ndim != 2:
                        raise ValueError("Expected unsigned 16-bit registered depth")
                    measured[depths.pop(member.name)] = depth_image
    if rgbs or depths:
        raise ValueError("Missing images from timestamp index")
    return frames, measured


def run(archive, sequence, out, count, steps, resolution):
    import cv2

    out.mkdir(parents=True, exist_ok=False)
    prefix = f"rgbd_dataset_{sequence}/"
    started = time.monotonic()
    frames, depths = read_sequence(archive, prefix, out / "input", count)
    prep_seconds = time.monotonic() - started
    started = time.monotonic()
    rough, links, _ = odometry(frames, depths, "hybrid")
    rough_seconds = time.monotonic() - started
    cv2.setRNGSeed(42)
    started = time.monotonic()
    refined, graph = refine(frames, depths, rough, links, solve_relative)
    graph_seconds = time.monotonic() - started
    identity = frame_identity(frames)
    models = {"hybrid": rough, "graph": refined}
    for label, poses in models.items():
        manifest = {"frames": identity, "world_from_camera": {str(i): p.tolist() for i, p in poses.items()}}
        (out / f"{label}.json").write_text(json.dumps(manifest))
    # Score both frozen estimates against independent motion capture on the same indices.
    with tarfile.open(archive) as bundle:
        truth = np.loadtxt(bundle.extractfile(prefix + "groundtruth.txt"))
    shared = sorted(rough.keys() & refined.keys())
    result = dict(sequence=sequence, input=count, depth=len(depths), registered=len(shared),
                  failed=sorted(set(range(count)) - set(shared)), archive_seconds=prep_seconds,
                  rough_seconds=rough_seconds, refine_seconds=graph_seconds, graph=graph,
                  holdout_indices=shared[::8])
    result["trajectory"] = {
        label: trajectory_score({i: poses[i] for i in shared}, truth, frames, False)
        for label, poses in models.items()
    }
    path = out / "results.json"
    path.write_text(json.dumps(result, indent=2))
    if steps:
        if len(shared) < 8:
            raise ValueError("Too few shared views for holdout training")
        for label, poses in models.items():
            work = (out / f"training-{label}").resolve()
            started = time.monotonic()
            scene, seeds = prepare(work, frames, depths, poses, shared)
            prep = time.monotonic() - started
            report = train_scene(scene, work, steps=steps, resolution=resolution)
            result[f"training_{label}"] = dict(depth_seeds=seeds, preparation_seconds=prep, **report)
            path.write_text(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sequence", choices=("freiburg1_desk", "freiburg1_xyz"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=96)
    parser.add_argument("--steps", type=int, default=1000, help="Set to zero for trajectory scoring only")
    parser.add_argument("--resolution", type=int, default=512)
    args = parser.parse_args()
    if not 8 <= args.frames <= 200 or args.steps < 0 or args.resolution < 16:
        parser.error("Expected 8–200 frames, nonnegative steps and resolution of at least 16")
    result = run(args.archive, args.sequence, args.out, args.frames, args.steps, args.resolution)
    for method, metrics in result["trajectory"].items():
        print(method, metrics["scored"], "poses;", metrics["center_rmse_m"], "m RMSE")


if __name__ == "__main__":
    main()
