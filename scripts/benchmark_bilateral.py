"""Test sparse, training-only bilateral color alignment on a frozen Brush scene."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import shutil
import time

import numpy as np
from PIL import Image

from scripts.benchmark_track_color import sample_rgb, unique_camera_elements
from scripts.benchmark_exports import sha, write_comparison_artifacts
from webscan import brush

GRID_SIZE = 4
LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
MAX_SHIFT = 8 / 255
ANCHOR_WEIGHT = 8.0


def grid_weights(positions):
    """Trilinear interpolation on an x, y, encoded-luminance lattice in [0, 1]."""
    scaled = np.clip(positions, 0, 1) * (GRID_SIZE - 1)
    base = np.minimum(scaled.astype(np.intp), GRID_SIZE - 2)
    frac = scaled - base
    for corner in np.ndindex(2, 2, 2):
        index = (base + corner).T
        weight = np.prod(np.where(corner, frac, 1 - frac), axis=1)
        yield np.ravel_multi_index(index, (GRID_SIZE,) * 3), weight


def fit_grid(positions, offsets):
    """Fit RGB translations, with a zero prior on every lattice vertex."""
    basis = np.zeros((len(positions), GRID_SIZE ** 3))
    for indices, weights in grid_weights(positions):
        basis[np.arange(len(indices)), indices] = weights
    system = basis.T @ basis + ANCHOR_WEIGHT * np.eye(GRID_SIZE ** 3)
    grid = np.linalg.solve(system, basis.T @ np.clip(offsets, -MAX_SHIFT, MAX_SHIFT))
    return np.clip(grid, -MAX_SHIFT, MAX_SHIFT)


def lookup(grid, positions):
    result = np.zeros((len(positions), 3))
    for indices, weights in grid_weights(positions):
        result += weights[:, None] * grid[indices]
    return result


def correct_image(pixels, grid):
    height, width = pixels.shape[:2]
    output = np.empty_like(pixels)
    for start in range(0, height, 64):
        rgb = pixels[start:start + 64].astype(np.float32) / 255
        y, x = np.indices(rgb.shape[:2], dtype=np.float32)
        positions = np.stack(((x + 0.5) / width, (y + start + 0.5) / height,
                              rgb @ LUMA), axis=-1)
        delta = lookup(grid, positions.reshape(-1, 3)).reshape(rgb.shape)
        output[start:start + len(rgb)] = np.rint(255 * np.clip(rgb + delta, 0, 1))
    return output


def sample_tracks(model, images):
    samples = defaultdict(list)
    for point_id, point in model.points3D.items():
        if not np.isfinite(point.error) or point.error >= 4:
            continue
        observations = []
        elements, _ = unique_camera_elements(point.track.elements)
        for elem in elements:
            iid = elem.image_id
            if iid not in images:
                continue
            image = model.images[iid]
            obs = image.points2D[elem.point2D_idx]
            if obs.point3D_id != point_id:
                raise ValueError("Nonreciprocal sparse track")
            cam = model.cameras[image.camera_id]
            xyz = image.cam_from_world() * point.xyz
            if not np.isfinite(xyz).all() or xyz[2] <= 0:
                continue
            projected = cam.img_from_cam(xyz)
            if projected is None or not np.linalg.norm(projected - obs.xy) <= 2:
                continue
            color = sample_rgb(images[iid], obs.xy)
            if color is not None:
                position = [obs.xy[0] / cam.width, obs.xy[1] / cam.height, color @ LUMA]
                observations.append((iid, position, color))
        if len(observations) < 3:
            continue
        colors = np.stack([rgb for _, _, rgb in observations])
        for idx, (iid, position, color) in enumerate(observations):
            target = np.median(np.delete(colors, idx, axis=0), axis=0)
            samples[iid].append((point_id, position, target - color))
    return samples


def prepare(source, folder, candidate):
    import pycolmap

    input_dir = source / "sfm/undistorted"
    scene = json.loads((source / "sfm/scene.json").read_text())
    report = json.loads((source / "artifacts/report.json").read_text())
    model = pycolmap.Reconstruction(input_dir / "sparse")
    views = sorted(scene["views"], key=lambda view: view["image_name"])
    ordered = sorted(model.images.items(), key=lambda pair: pair[1].name)
    if [im.name for _, im in ordered] != [v["image_name"] for v in views]:
        raise ValueError("Scene and sparse camera names differ")
    holdouts = {r["name"] for r in report["evaluation_images"]}
    expected = {Path(view["image_name"]).stem + ".png" for view in views[::8]}
    if not report["evaluation_verified"] or holdouts != expected or report["training_views"] != len(views) - len(expected):
        raise ValueError("Requires a verified every-eighth holdout split")

    for view, record in zip(views[::8], sorted(report["evaluation_images"], key=lambda r: r["name"])):
        if sha(input_dir / "images" / view["image_name"]) != record["reference_sha256"]:
            raise ValueError("Held-out reference bytes differ from the frozen job")

    pixels = {}
    for index, (iid, img) in enumerate(ordered):
        if index % 8:
            with Image.open(input_dir / "images" / img.name) as im:
                pixels[iid] = np.asarray(im.convert("RGB"))
    samples = sample_tracks(model, pixels) if candidate else {}
    output = folder / "sfm/undistorted"
    (output / "images").mkdir(parents=True)
    (output / "sparse").mkdir()
    errors = []
    fit_count = 0
    for (iid, img), view in zip(ordered, views):
        name = img.name
        if iid in pixels:
            rgb = pixels.pop(iid)
            if samples.get(iid):
                ids, positions, offsets = zip(*samples[iid])
                positions, offsets = np.asarray(positions), np.asarray(offsets)
                # Reserve entire 3D tracks for a correspondence check across cameras.
                check = np.asarray(ids) % 5 == 0
                grid = fit_grid(positions[~check], offsets[~check])
                if np.any(check):
                    residual = offsets[check] - lookup(grid, positions[check])
                    errors.extend(zip(np.abs(offsets[check]).mean(axis=1),
                                      np.abs(residual).mean(axis=1)))
                fit_count += int((~check).sum())
                rgb = correct_image(rgb, grid)
            name = Path(name).stem + ".png"
            img.name = name
            Image.fromarray(rgb).save(output / "images" / name, compress_level=1)
        else:
            shutil.copyfile(input_dir / "images" / name, output / "images" / name)
        view.update(image_name=name, image=str(output / "images" / name))
    model.write_binary(output / "sparse")
    (folder / "sfm/scene.json").write_text(json.dumps(scene))
    return dict(fit_observations=fit_count, check_observations=len(errors),
                check_mean_abs_rgb_before_after=np.mean(errors, axis=0).tolist() if errors else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Completed held-out Brush job")
    parser.add_argument("--output", required=True, type=Path, help="New experiment job")
    parser.add_argument("--variant", required=True, choices=["control", "grid"])
    args = parser.parse_args()
    work = args.output.resolve()
    work.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    stats = prepare(args.source, work, args.variant == "grid")
    stats["prepare_seconds"] = time.monotonic() - start
    scene = json.loads((work / "sfm/scene.json").read_text())
    config = dict(steps=3000, max_points=150000, resolution=512, evaluation_mode="holdout")
    before_training = time.monotonic()
    report = brush.train(scene, work, work / "artifacts", config, lambda *_: None)
    stats["training_export_seconds"] = time.monotonic() - before_training
    write_comparison_artifacts(work, scene, report)
    stats.update(reuse_total_seconds=time.monotonic() - start, variant=args.variant,
                 source_job=args.source.name, ply_bytes=(work / "artifacts/gaussians.ply").stat().st_size)
    (work / "experiment.json").write_text(json.dumps(stats, indent=2))
    print(work.name, stats, report["evaluation"], flush=True)


if __name__ == "__main__":
    main()
