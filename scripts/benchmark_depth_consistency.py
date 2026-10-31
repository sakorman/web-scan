"""Screen calibrated relative depths across training views without adding points.

NumPy-only diagnostic. Geometric agreement is not an independent depth truth.
Pixel coordinates follow COLMAP's corner origin; tolerances use native pixels.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np

from scripts.benchmark_depth_prior import digest, sample_depth, save


def camera(view):
    if view["camera_model"] != "PINHOLE":
        raise ValueError("Cross-view screening requires undistorted PINHOLE cameras")
    intrinsics = np.asarray(view["camera_params"], dtype=np.float64)
    pose = np.asarray(view["cam_from_world"], dtype=np.float64)
    if (intrinsics.shape != (4,) or pose.shape != (3, 4)
            or not np.isfinite(intrinsics).all() or not np.isfinite(pose).all()
            or np.any(intrinsics[:2] <= 0)
            or not np.allclose(pose[:, :3] @ pose[:, :3].T, np.eye(3), atol=1e-6, rtol=0)
            or not np.isclose(np.linalg.det(pose[:, :3]), 1, atol=1e-6, rtol=0)):
        raise ValueError("Invalid camera calibration or rigid pose")
    return intrinsics, pose


def unproject(xy, z, calibration):
    intrinsics, pose = calibration
    rays = np.column_stack([(xy - intrinsics[2:]) / intrinsics[:2], np.ones(len(xy))])
    return (rays * z[:, None] - pose[:, 3]) @ pose[:, :3]


def project(points, calibration):
    intrinsics, pose = calibration
    local = points @ pose[:, :3].T + pose[:, 3]
    with np.errstate(divide="ignore", invalid="ignore"):
        xy = local[:, :2] / local[:, 2, None] * intrinsics[:2] + intrinsics[2:]
    return xy, local[:, 2]


def check_pair(xy, z, source_camera, target_camera, target_depth, config):
    source_valid = np.isfinite(z) & (z > 0) & np.isfinite(xy).all(axis=1)
    points = unproject(xy, z, source_camera)
    target_xy, projected_z = project(points, target_camera)
    sampled_z, inside = sample_depth(target_depth, target_xy)
    visible = source_valid & inside & (projected_z > 0) & np.isfinite(sampled_z) & (sampled_z > 0)
    # "Visible" here means sampleable projection, not proven physical visibility.
    back_points = unproject(target_xy, sampled_z, target_camera)
    back_xy, back_z = project(back_points, source_camera)
    with np.errstate(divide="ignore", invalid="ignore"):
        signed_error = (projected_z - sampled_z) / sampled_z
        back_error = np.abs(back_z / z - 1)
    pixel_error = np.linalg.norm(back_xy - xy, axis=1)
    centers = [-cal[1][:, :3].T @ cal[1][:, 3] for cal in (source_camera, target_camera)]
    ray1, ray2 = points - centers[0], points - centers[1]
    angle = np.degrees(np.arctan2(np.linalg.norm(np.cross(ray1, ray2), axis=1), (ray1 * ray2).sum(axis=1)))
    tolerance = config["relative_depth_tolerance"]
    occlusion = visible & (signed_error > tolerance)
    conflict = visible & (signed_error < -tolerance)
    depth_band = visible & ~occlusion & ~conflict
    roundtrip = ((back_z > 0) & (back_error <= tolerance)
                 & (pixel_error <= config["roundtrip_pixels"]))
    parallax = angle >= config["min_parallax_degrees"]
    masks = {
        "invalid_source": ~source_valid,
        "unavailable_target": source_valid & ~visible,
        "occlusion_candidate": occlusion,
        "front_depth_conflict": conflict,
        "roundtrip_failed": depth_band & ~roundtrip,
        "low_parallax": depth_band & roundtrip & ~parallax,
        "supported": depth_band & roundtrip & parallax,
    }
    assert np.all(np.sum(list(masks.values()), axis=0) == 1)
    return masks


def grid(width, height, step):
    x = np.arange(step // 2, width, step) + 0.5
    y = np.arange(step // 2, height, step) + 0.5
    xx, yy = np.meshgrid(x, y)
    return np.column_stack([xx.ravel(), yy.ravel()]), xx.shape


def run(args):
    started = time.monotonic()
    config = json.loads(args.config.read_text())["cross_view"]
    if (config["grid_step"] < 1 or config["min_other_views"] < 1
            or not 0 < config["relative_depth_tolerance"] < 1
            or config["roundtrip_pixels"] < 0 or config["min_parallax_degrees"] < 0):
        raise ValueError("Invalid cross-view screening parameters")
    args.output.mkdir(parents=True, exist_ok=False)
    analysis = json.loads(args.analysis.read_text())
    assert analysis["anchor_sha256"] == digest(args.anchors)
    views = json.loads(args.anchors.read_text())["views"]
    rows = {(v["job"], v["name"]): v for v in analysis["views"]}
    predictions = {(v["job"], v["name"]): v for v in analysis["inference"]["views"]}
    assert len(rows) == len(predictions) == len(views)
    keys = {(v["job"], v["name"]) for v in views}
    assert len(keys) == len(views) and keys == set(rows) == set(predictions)
    assert analysis["inference"]["status"] == "complete"
    assert analysis["inference"]["anchor_sha256"] == digest(args.anchors)
    results = {"config": config, "analysis_sha256": digest(args.analysis),
               "anchors_sha256": digest(args.anchors), "scope": "training views only; no point injection; no independent depth truth",
               "status": "running", "scenes": []}
    try:
        for job in sorted({v["job"] for v in views}):
            image_hashes = [v["image_sha256"] for v in views if v["job"] == job]
            if len(set(image_hashes)) != len(image_hashes):
                raise ValueError("Duplicate training image cannot count as another view")
            selected = []
            excluded = []
            for view in [v for v in views if v["job"] == job]:
                key = (job, view["name"])
                row, pred = rows[key], predictions[key]
                if not row["passed"]:
                    excluded.append({"name": view["name"], "reason": row["reason"]})
                    continue
                path = args.predictions / pred["prediction"]
                assert digest(path) == pred["prediction_sha256"]
                assert digest(view["image"]) == pred["image_sha256"] == view["image_sha256"]
                prediction = np.load(path, allow_pickle=False)
                assert prediction.shape == (view["height"], view["width"])
                inverse = row["scale"] * prediction.astype(np.float64) + row["shift"]
                depth = np.full(inverse.shape, np.nan)
                np.divide(1, inverse, out=depth, where=np.isfinite(inverse) & (inverse > 0))
                selected.append({**view, "depth": depth, "camera": camera(view)})
            scene = {"job": job, "selected_views": len(selected), "excluded_views": excluded,
                     "views": [], "pairs": []}
            for source in selected:
                xy, shape = grid(source["width"], source["height"], config["grid_step"])
                z, inside = sample_depth(source["depth"], xy)
                valid = inside & np.isfinite(z) & (z > 0)
                votes = np.zeros(len(xy), dtype=np.uint16)
                projected = np.zeros_like(votes)
                occlusions = np.zeros_like(votes)
                conflicts = np.zeros_like(votes)
                for target in selected:
                    if target["name"] == source["name"]:
                        continue
                    masks = check_pair(xy, z, source["camera"], target["camera"], target["depth"], config)
                    votes += masks["supported"]
                    projected += ~(masks["invalid_source"] | masks["unavailable_target"])
                    occlusions += masks["occlusion_candidate"]
                    conflicts += masks["front_depth_conflict"]
                    scene["pairs"].append({"source": source["name"], "target": target["name"],
                                           "counts": {k: int(v.sum()) for k, v in masks.items()}})
                retained = valid & (votes >= config["min_other_views"])
                name = f"{job}-{Path(source['name']).stem}.npz"
                np.savez_compressed(args.output / name, support=votes.reshape(shape),
                                    projected=projected.reshape(shape), occlusion=occlusions.reshape(shape),
                                    conflict=conflicts.reshape(shape), valid=valid.reshape(shape))
                row = {"name": source["name"], "grid_shape": list(shape), "grid_samples": len(xy),
                       "valid_samples": int(valid.sum()), "retained_samples": int(retained.sum()),
                       "retained_fraction": float(retained.sum() / valid.sum()) if valid.any() else 0.,
                       "zero_support": int((valid & (votes == 0)).sum()),
                       "one_support": int((valid & (votes == 1)).sum()),
                       "no_projected_target": int((valid & (projected == 0)).sum()),
                       "enough_projected_targets": int((valid & (projected >= config["min_other_views"])).sum()),
                       "retained_with_front_conflict": int((retained & (conflicts > 0)).sum()),
                       "with_occlusion_candidate": int((valid & (occlusions > 0)).sum()),
                       "with_front_conflict": int((valid & (conflicts > 0)).sum()),
                       "map": name, "map_sha256": digest(args.output / name)}
                scene["views"].append(row)
            scene["baseline_samples"] = sum(v["valid_samples"] for v in scene["views"])
            scene["retained_samples"] = sum(v["retained_samples"] for v in scene["views"])
            scene["enough_projected_targets"] = sum(v["enough_projected_targets"] for v in scene["views"])
            scene["retained_fraction"] = scene["retained_samples"] / scene["baseline_samples"] if scene["baseline_samples"] else 0.
            results["scenes"].append(scene)
            print(job, scene["selected_views"], scene["retained_samples"], "/", scene["baseline_samples"], flush=True)
        results["status"] = "complete"
    except BaseException as error:
        results.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        results["seconds"] = time.monotonic() - started
        save(args.output / "consistency.json", results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("anchors", "analysis", "predictions", "config", "output"):
        parser.add_argument(f"--{flag}", type=Path, required=True)
    run(parser.parse_args())
