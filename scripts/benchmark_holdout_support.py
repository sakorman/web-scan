"""Join aligned holdout error tiles with observed COLMAP training tracks.

Sparse feature support is not dense surface visibility or geometric ground truth.
Run with python -m scripts.benchmark_holdout_support.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time
import subprocess
import sys
from tempfile import TemporaryDirectory

import numpy as np


def tile_support(model, image_id, train_ids, dimensions, divisions):
    image = model.images[image_id]
    camera = model.cameras[image.camera_id]
    width, height = dimensions
    if not 1 <= divisions <= min(16, width, height):
        raise ValueError("Grid must fit scoring dimensions")
    if image_id in train_ids:
        raise ValueError("Target must be held out from training")
    tiles = []
    for row in range(divisions):
        for col in range(divisions):
            tiles.append({
                "xyxy": [col * width // divisions, row * height // divisions,
                         (col + 1) * width // divisions, (row + 1) * height // divisions],
                "keypoints": 0, "tracks": 0, "with_training": 0, "with_two_training": 0,
                "sources": Counter(), "occupied": set(),
            })
    rejected = 0
    seen = set()
    for obs in image.points2D:
        x, y = np.asarray(obs.xy) * [width / camera.width, height / camera.height]
        inside = np.isfinite([x, y]).all() and 0 <= x < width and 0 <= y < height
        tile = None
        if inside:
            # Integer floor boundaries differ from fractional cells at odd sizes.
            tile = next(t for t in tiles if t["xyxy"][0] <= x < t["xyxy"][2]
                        and t["xyxy"][1] <= y < t["xyxy"][3])
            tile["keypoints"] += 1
        if not obs.has_point3D() or obs.point3D_id in seen:
            continue
        seen.add(obs.point3D_id)
        point = model.points3D[obs.point3D_id]
        if (not np.isfinite(point.error) or point.error >= 4
                or point.track.length() < 2 or not inside):
            rejected += 1
            continue
        sources = set()
        for elem in point.track.elements:
            if elem.image_id not in train_ids:
                continue
            other = model.images[elem.image_id].points2D[elem.point2D_idx]
            if not other.has_point3D() or other.point3D_id != obs.point3D_id:
                raise ValueError("Broken reciprocal track")
            sources.add(elem.image_id)
        tile["tracks"] += 1
        tile["with_training"] += bool(sources)
        tile["with_two_training"] += len(sources) >= 2
        tile["sources"].update(sources)
        if sources:
            x0, y0, x1, y1 = tile["xyxy"]
            tile["occupied"].add((int(4 * (x - x0) / (x1 - x0)),
                                  int(4 * (y - y0) / (y1 - y0))))
    for tile in tiles:
        tile["training_sources"] = [
            {"image_name": model.images[key].name, "shared_tracks": count}
            for key, count in sorted(tile.pop("sources").items(),
                                     key=lambda item: (-item[1], model.images[item[0]].name))
        ]
        tile["occupied_subcells_of_16"] = len(tile.pop("occupied"))
    return {"tiles": tiles, "rejected_linked_observations": rejected}


def measure(data, job, divisions=4):
    import pycolmap

    start = time.perf_counter()
    work = data / "jobs" / job
    report = json.loads((work / "artifacts/report.json").read_text())
    if report.get("evaluation_mode") != "holdout":
        raise ValueError("Requires explicit holdout evaluation")
    # Reuses existing camera, image identity and complete evaluation checks.
    # PyCOLMAP and Torch can load distinct OpenMP runtimes on macOS.
    # Keep the existing image scorer in its own process.
    with TemporaryDirectory(prefix="webscan-support-") as folder:
        path = Path(folder) / "scores.json"
        subprocess.run(
            [sys.executable, "-m", "scripts.compare_quality", "--data", str(data),
             "--quick", job, "--detail", job, "--error-grid", str(divisions),
             "--output", str(path)],
            check=True, stdout=subprocess.DEVNULL,
        )
        scored = json.loads(path.read_text())
    scene = json.loads((work / "sfm/scene.json").read_text())
    sparse = work / "sfm/undistorted/sparse"
    model = pycolmap.Reconstruction(sparse)
    images = sorted(model.images.items(), key=lambda pair: pair[1].name)
    ordered = sorted(scene["views"], key=lambda view: view["image_name"])
    if [im.name for _, im in images] != [v["image_name"] for v in ordered]:
        raise ValueError("Sparse image set differs from training scene")
    for (_, image), view in zip(images, ordered):
        camera = model.cameras[image.camera_id]
        pose = image.cam_from_world().matrix().astype(np.float32)
        center = np.asarray(scene["normalization"]["center"], dtype=np.float32)
        pose[:, 3] = (pose[:, :3] @ center + pose[:, 3]) / scene["normalization"]["scale"]
        if (camera.model.name != "PINHOLE"
                or (camera.width, camera.height) != (view["width"], view["height"])
                or not np.allclose(pose, np.asarray(view["view"])[:3], rtol=0, atol=2e-6)
                or not np.allclose(camera.calibration_matrix(), view["K"], rtol=0, atol=1e-9)):
            raise ValueError("Sparse camera differs from training scene")
    training_ids = {key for index, (key, _) in enumerate(images) if index % 8}
    if len(training_ids) != report["training_views"]:
        raise ValueError("Training split mismatch")
    views = []
    for (image_id, image), score in zip(images[::8], scored["views"], strict=True):
        if Path(image.name).stem != Path(score["view"]).stem:
            raise ValueError("Scoring view mismatch")
        size = score["scoring_dimensions"]
        summary = tile_support(model, image_id, training_ids, (size["width"], size["height"]), divisions)
        for tile, error in zip(summary["tiles"], score["quick"]["error_tiles"], strict=True):
            assert tile["xyxy"] == error["xyxy"]
            tile["mse"] = error["mse"]
        views.append({"image_name": image.name, "scoring_dimensions": size,
                      "psnr": score["quick"]["psnr"], "ssim": score["quick"]["ssim"], **summary})
    return {"job": job, "pycolmap_version": pycolmap.__version__, "grid": divisions,
            "point_error_max_exclusive_px": 4, "subgrid": 4,
            "split": "sorted names; every eighth held out; all photos participate in SfM",
            "sparse_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted(sparse.glob("*.bin"))},
            "mean": scored["mean"]["quick"], "views": views,
            "analysis_seconds": time.perf_counter() - start}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=Path(".data"))
    p.add_argument("--job", required=True)
    p.add_argument("--grid", type=int, default=4)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = measure(args.data, args.job, args.grid)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
