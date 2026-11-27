"""Prepare a posed COLMAP capture from ARKitScenes raw wide RGB, trajectory and depth."""
import argparse
import io
import json
import time
from contextlib import ExitStack
from pathlib import Path
from zipfile import ZipFile

import numpy as np
from PIL import Image


def indexed(archive, suffix):
    result = {}
    for name in archive.namelist():
        if name.endswith(suffix) and not name.startswith("__MACOSX/"):
            stamp = float(Path(name).stem.rsplit("_", 1)[1])
            if stamp in result:
                raise ValueError(f"Repeated timestamp {stamp}")
            result[stamp] = name
    return result


def sample_frames(trajectory, timestamps, count):
    if (trajectory.ndim != 2 or trajectory.shape[1] != 7 or len(trajectory) < count
            or not np.isfinite(trajectory).all() or np.any(np.diff(trajectory[:, 0]) <= 0)):
        raise ValueError("Expected a finite chronological ARKitScenes trajectory")
    times = np.asarray(sorted(timestamps))
    if not len(times):
        raise ValueError("Missing synchronized observations")
    selected = []
    for index in np.linspace(0, len(trajectory) - 1, count).round().astype(int):
        t = trajectory[index, 0]
        pos = np.searchsorted(times, t)
        candidates = times[max(0, pos - 1):pos + 1]
        stamp = min(candidates, key=lambda x: abs(t - x))
        if abs(t - stamp) > .02 or (selected and stamp <= selected[-1][1]):
            raise ValueError("A selected pose has no unique observation within 20 ms")
        selected.append((index, stamp))
    return selected


def prepare(assets, out, frames):
    import pycolmap

    if not 8 <= frames <= 200:
        raise ValueError("Choose 8–200 views for an every-eighth holdout")
    trajectory = np.loadtxt(assets / "lowres_wide.traj", ndmin=2)
    started = time.monotonic()
    out.mkdir(parents=True, exist_ok=False)
    sparse = out / "sparse"
    photos = out / "images"
    sparse.mkdir()
    photos.mkdir()
    stamps = []
    point_id = 0
    camera_ids = {}
    with ExitStack() as stack:
        archives = [stack.enter_context(ZipFile(assets / f"{name}.zip")) for name in
                    ("lowres_wide", "lowres_wide_intrinsics", "lowres_depth")]
        rgb_names, k_names, depth_names = [indexed(z, s) for z, s in zip(archives, (".png", ".pincam", ".png"))]
        selected = sample_frames(trajectory, set(rgb_names) & set(k_names) & set(depth_names), frames)
        views = stack.enter_context((sparse / "images.txt").open("w"))
        cameras = stack.enter_context((sparse / "cameras.txt").open("w"))
        points = stack.enter_context((sparse / "points3D.txt").open("w"))
        for position, (index, stamp) in enumerate(selected):
            row = trajectory[index]
            # ARKitScenes stores the world-to-camera angle-axis and translation.
            rotation = pycolmap.Rotation3d(row[1:4])
            offset = row[4:]
            width, height, fx, fy, cx, cy = np.loadtxt(io.BytesIO(archives[1].read(k_names[stamp])))
            if (min(fx, fy) <= 0 or not np.isfinite([width, height, fx, fy, cx, cy]).all()):
                raise ValueError("Invalid per-frame calibration")
            with Image.open(io.BytesIO(archives[0].read(rgb_names[stamp]))) as image:
                if image.size != (width, height):
                    raise ValueError("RGB dimensions differ from measured intrinsics")
                rgb = np.array(image.convert("RGB"))
            name = f"{position:04d}-{stamp:.3f}.jpg"
            Image.fromarray(rgb).save(photos / name, quality=95, subsampling=0)
            camera = (int(width), int(height), fx, fy, cx + .5, cy + .5)
            if camera not in camera_ids:
                camera_ids[camera] = len(camera_ids) + 1
                cameras.write(f"{camera_ids[camera]} PINHOLE " + " ".join(map(str, camera)) + "\n")
            qx, qy, qz, qw = rotation.quat
            image_id = position + 1
            fields = [image_id, qw, qx, qy, qz, *offset, camera_ids[camera], name]
            views.write(" ".join(map(str, fields)) + "\n")
            measurements = []
            if position % 8:
                with Image.open(io.BytesIO(archives[2].read(depth_names[stamp]))) as image:
                    depth = np.array(image)
                if depth.dtype != np.uint16 or depth.shape != rgb.shape[:2]:
                    raise ValueError("Expected aligned uint16 millimeter depth")
                y, x = np.mgrid[8:depth.shape[0]:16, 8:depth.shape[1]:16]
                z = depth[y, x] / 1000.
                valid = (z > .25) & (z < 5.)
                x, y, z = x[valid], y[valid], z[valid]
                local = np.column_stack(((x-cx)*z/fx, (y-cy)*z/fy, z))
                world = (local - offset) @ rotation.matrix()
                for j, (xyz, color) in enumerate(zip(world, rgb[y, x])):
                    point_id += 1
                    fields = [point_id, *xyz, *color, 0, image_id, j]
                    points.write(" ".join(map(str, fields)) + "\n")
                    measurements.append(f"{x[j]+.5} {y[j]+.5} {point_id}")
            views.write(" ".join(measurements) + "\n")
            stamps.append(dict(index=position, rgb_seconds=stamp, pose_seconds=row[0]))
    if not point_id:
        raise ValueError("No valid training-view depth points")
    report = dict(input=frames, cameras=len(camera_ids), train_indices=[i for i in range(frames) if i%8],
                  holdout_indices=list(range(0, frames, 8)), depth_seeds=point_id, timestamps=stamps,
                  preparation_seconds=time.monotonic()-started)
    (out / "preparation.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=96)
    args = parser.parse_args()
    prepared = prepare(args.assets, args.out, args.frames)
    print(prepared["input"], "views,", prepared["depth_seeds"], "training depth points")
