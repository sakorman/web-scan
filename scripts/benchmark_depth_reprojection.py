"""Reproject grouped depth representatives into complete native depth maps.

This is an audit only: it never changes a reconstruction or selects points for
training.  Unlike the grid diagnostic, target pixels need not land on the
sampling grid.  A target depth is support only when the projected point is
inside the image and agrees in camera-Z within a fixed relative tolerance.
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np

from scripts.benchmark_depth_prior import digest, sample_depth, save
from scripts.benchmark_depth_support import raw_project, radial_camera
from scripts.benchmark_depth_consistency import camera, project
from scripts.benchmark_sensor_depth import sensor_samples


def load_depths(analysis, predictions, names, views):
    alignment = {v["name"]: v for v in analysis["views"]}
    inferred = {v["name"]: v for v in analysis["inference"]["views"]}
    result = {}
    for name in names:
        row, pred = alignment[name], inferred[name]
        meta = views[name]
        path = predictions / pred["prediction"]
        if digest(path) != pred["prediction_sha256"]:
            raise ValueError("Prediction identity mismatch")
        raw = np.load(path, allow_pickle=False)
        if raw.shape != (meta["height"], meta["width"]):
            raise ValueError("Prediction dimensions differ from camera")
        inverse = row["scale"] * raw.astype(np.float64) + row["shift"]
        depth = np.full(raw.shape, np.nan, dtype=np.float64)
        np.divide(1, inverse, out=depth, where=np.isfinite(inverse) & (inverse > 0))
        result[name] = depth
    return result


def calibration(view):
    if view["camera_model"] == "RADIAL":
        return radial_camera(view), raw_project
    if view["camera_model"] == "PINHOLE":
        return camera(view), project
    raise ValueError("Unsupported camera model")


def support_mask(projected_z, sampled_z, inside, tolerance):
    """Classify a projected point against a native depth sample."""
    projected_z = np.asarray(projected_z, dtype=float)
    sampled_z = np.asarray(sampled_z, dtype=float)
    inside = np.asarray(inside, dtype=bool)
    if projected_z.shape != sampled_z.shape or inside.shape != projected_z.shape:
        raise ValueError("Projection arrays differ")
    if not np.isfinite(tolerance) or tolerance <= 0 or tolerance >= 1:
        raise ValueError("Invalid tolerance")
    valid = inside & np.isfinite(projected_z) & (projected_z > 0)
    valid &= np.isfinite(sampled_z) & (sampled_z > 0)
    return valid & (np.abs(sampled_z / projected_z - 1) <= tolerance)


def summarize_variant(group_json, group_map, samples, views, depths, sensor_depths=None,
                      sensor_scale=None, tolerance=0.05):
    samples_by_key = {}
    for index, row in enumerate(samples["views"]):
        path = samples["_dir"] / row["map"]
        if digest(path) != row["map_sha256"]:
            raise ValueError("Sample identity mismatch")
        with np.load(path, allow_pickle=False) as values:
            if not np.isfinite(values["world"]).all() or not np.isfinite(values["z"]).all():
                raise ValueError("Non-finite sample")
            for world, z, grid in zip(values["world"], values["z"], values["grid_index"]):
                key = (index, int(grid))
                if key in samples_by_key:
                    raise ValueError("Repeated sample identity")
                samples_by_key[key] = (world, z)
    keys = list(zip(group_map["source"].astype(int), group_map["grid_index"].astype(int)))
    if len(set(keys)) != len(keys) or any(key not in samples_by_key for key in keys):
        raise ValueError("Group/sample identity differs")
    world = np.asarray([samples_by_key[key][0] for key in keys], dtype=float)
    source = group_map["source"].astype(int)
    leader_ids = group_map["leaders"].astype(int)
    if np.any(leader_ids < 0) or np.any(leader_ids >= len(world)):
        raise ValueError("Invalid group leaders")
    view_names = [v["name"] for v in samples["views"]]
    cameras = {}
    projectors = {}
    for name in view_names:
        cameras[name], projectors[name] = calibration(views[name])
    usable = np.zeros(len(leader_ids), dtype=np.uint16)
    supported = np.zeros(len(leader_ids), dtype=np.uint16)
    sensor_good = np.zeros(len(leader_ids), dtype=np.uint16)
    target_total = 0
    target_sensor_total = 0
    for group, leader in enumerate(leader_ids):
        point = world[leader][None, :]
        source_name = view_names[source[leader]]
        source_support = 0
        source_sensor = 0
        for target_name in view_names:
            if target_name == source_name:
                continue
            xy, projected_z = projectors[target_name](point, cameras[target_name])
            sampled, inside = sample_depth(depths[target_name], xy)
            valid = inside & (projected_z > 0) & np.isfinite(sampled) & (sampled > 0)
            target_total += int(valid[0])
            source_support += int(support_mask(projected_z, sampled, valid, tolerance)[0])
            if sensor_depths is not None:
                measured, sensor_valid = sensor_samples(sensor_depths[target_name], xy)
                sensor_valid &= valid
                target_sensor_total += int(sensor_valid[0])
                source_sensor += int(support_mask(projected_z, measured / sensor_scale, sensor_valid, tolerance)[0])
        usable[group] = sum(1 for target_name in view_names if target_name != source_name)
        supported[group] = source_support
        sensor_good[group] = source_sensor
    return {
        "groups": int(len(leader_ids)),
        "group_source_views": group_json["summary"]["groups_by_views"],
        "prior_diverse_three": int(group_json["summary"]["diverse_three"]),
        "target_checks": int(target_total),
        "groups_with_two_support": int((supported >= 2).sum()),
        "groups_with_three_support": int((supported >= 3).sum()),
        "groups_with_two_support_and_prior_diverse": int(((supported >= 2) & np.array([r["diverse_three"] for r in group_json["groups"]])).sum()),
        "sensor_target_checks": int(target_sensor_total) if sensor_depths is not None else None,
        "sensor_groups_with_two_support": int((sensor_good >= 2).sum()) if sensor_depths is not None else None,
        "sensor_groups_with_three_support": int((sensor_good >= 3).sum()) if sensor_depths is not None else None,
    }


def run(args):
    started = time.monotonic()
    samples = json.loads((args.samples / "samples.json").read_text())
    samples["_dir"] = args.samples
    groups = json.loads((args.groups / "groups.json").read_text())
    anchors = json.loads(args.anchors.read_text())["views"]
    analysis = json.loads(args.analysis.read_text())
    names = [v["name"] for v in samples["views"]]
    by_name = {v["name"]: v for v in anchors if v["job"] == args.job and v["name"] in names}
    if set(names) != set(by_name) or len(names) != len(set(names)):
        raise ValueError("Sample and camera view sets differ")
    depths = load_depths(analysis, args.predictions, names, by_name)
    sensor_depths = None
    sensor_scale = None
    if args.sensor_manifest:
        manifest = json.loads(args.sensor_manifest.read_text())
        rows = {v["name"]: v for v in manifest["views"]}
        if set(rows) != set(names):
            raise ValueError("Sensor and camera view sets differ")
        sensor_depths = {}
        for name, row in rows.items():
            path = args.sensor_manifest.parent / row["depth"]
            if digest(path) != row["depth_sha256"]:
                raise ValueError("Sensor depth identity mismatch")
            from PIL import Image
            sensor_depths[name] = np.asarray(Image.open(path))
        sensor_scale = args.sensor_scale
        if not sensor_scale or sensor_scale <= 0:
            raise ValueError("Missing positive sensor scale")
    output = {"status": "running", "job": args.job, "groups_sha256": digest(args.groups / "groups.json"),
              "samples_sha256": digest(args.samples / "samples.json"), "tolerance": args.tolerance,
              "scope": "native projected depth support; no point injection or training"}
    variants = []
    for variant in groups["variants"]:
        with np.load(args.groups / variant["map"], allow_pickle=False) as group_map:
            arrays = dict(group_map)
        summary = summarize_variant(variant, arrays, samples, by_name, depths, sensor_depths,
                                    sensor_scale, args.tolerance)
        variants.append({"radius": variant["radius"], "order": variant["order"], "summary": summary})
    output["variants"] = variants
    output["status"] = "complete"
    output["seconds"] = time.monotonic() - started
    save(args.output, output)
    print(json.dumps(output, indent=2))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--groups", type=Path, required=True)
    parser.add_argument("--anchors", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sensor-manifest", type=Path)
    parser.add_argument("--sensor-scale", type=float)
    parser.add_argument("--tolerance", type=float, default=0.05)
    run(parser.parse_args())
