"""Offline relative-depth screening; never changes a reconstruction or adds points.

Run anchors, infer and analyze in separate processes: PyCOLMAP and PyTorch can
load incompatible OpenMP runtimes. Optional inference dependencies stay external.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np


GATE = {"min_anchors_per_split": 20, "max_invalid_dense_fraction": 0.01,
        "max_check_median_abs_rel": 0.10, "max_check_p90_abs_rel": 0.25}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def sample_depth(depth, xy):
    """Bilinear samples at COLMAP corner-based coordinates, without edge padding."""
    xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2) - 0.5
    h, w = depth.shape
    valid = np.isfinite(xy).all(axis=1) & (xy[:, 0] >= 0) & (xy[:, 0] <= w - 1)
    valid &= (xy[:, 1] >= 0) & (xy[:, 1] <= h - 1)
    samples = np.full(len(xy), np.nan)
    x, y = xy[valid].T
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    dx, dy = x - x0, y - y0
    samples[valid] = ((1-dx)*(1-dy)*depth[y0, x0] + dx*(1-dy)*depth[y0, x1]
                      + (1-dx)*dy*depth[y1, x0] + dx*dy*depth[y1, x1])
    return samples, valid


def fit_cells(xy, width, height):
    """Even cells fit, odd cells check in a fixed 4 by 4 spatial checkerboard."""
    cells = np.floor(np.asarray(xy) / [width, height] * 4).astype(int)
    return np.sum(cells, axis=1) % 2 == 0


def camera_depth(pose, xyz):
    """Axial Z in the raw SfM camera frame, not ray distance or world Z."""
    return float((pose[:, :3] @ xyz + pose[:, 3])[2])


def relative_error(prediction, reference):
    errors = np.abs(prediction / reference - 1)
    return {"median_abs_rel": float(np.median(errors)),
            "p90_abs_rel": float(np.percentile(errors, 90)),
            "mean_abs_rel": float(np.mean(errors))}


def analyze_view(depth, xy, z):
    """Fit inverse camera Z on one spatial split and score the other.

    All excluded anchors and invalid check predictions are counted explicitly.
    A gate pass is only a screening result at sparse SfM support locations.
    """
    xy, z = np.asarray(xy).reshape(-1, 2), np.asarray(z)
    samples, inside = sample_depth(depth, xy)
    eligible = inside & np.isfinite(samples) & np.isfinite(z) & (z > 0)
    xy, samples, z = xy[eligible], samples[eligible], z[eligible]
    fit = fit_cells(xy, depth.shape[1], depth.shape[0])
    row = {"anchors": len(eligible), "excluded_anchors": int((~eligible).sum()),
           "fit_anchors": int(fit.sum()), "check_anchors": int((~fit).sum()), "passed": False}
    if min(row["fit_anchors"], row["check_anchors"]) < GATE["min_anchors_per_split"]:
        return {**row, "reason": "insufficient_spatial_anchors"}
    row["constant_depth_check"] = relative_error(np.full((~fit).sum(), np.median(z[fit])), z[~fit])
    matrix = np.column_stack([samples[fit], np.ones(fit.sum())])
    coeff, _, rank, _ = np.linalg.lstsq(matrix, 1 / z[fit], rcond=None)
    if rank != 2 or not np.isfinite(coeff).all():
        return {**row, "reason": "degenerate_fit"}
    a, b = coeff
    row.update(scale=float(a), shift=float(b))
    if a <= 0:
        return {**row, "reason": "nonpositive_scale"}
    inverse = a * np.asarray(depth, dtype=np.float64) + b
    row["invalid_dense_fraction"] = float((~np.isfinite(inverse) | (inverse <= 0)).mean())
    check_inverse = a * samples[~fit] + b
    valid = np.isfinite(check_inverse) & (check_inverse > 0)
    row["invalid_check_anchors"] = int((~valid).sum())
    if not valid.all():
        return {**row, "reason": "invalid_check_depth"}
    row["check"] = relative_error(1 / check_inverse, z[~fit])
    row["passed"] = (row["invalid_dense_fraction"] <= GATE["max_invalid_dense_fraction"]
                     and row["check"]["median_abs_rel"] <= GATE["max_check_median_abs_rel"]
                     and row["check"]["p90_abs_rel"] <= GATE["max_check_p90_abs_rel"])
    row["reason"] = "screen_pass" if row["passed"] else "screen_threshold_failed"
    return row


def anchors(args):
    import pycolmap
    from PIL import Image

    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    for work in args.jobs:
        work = work.resolve()
        result = json.loads((work / "result.json").read_text())
        assert result["status"] == "complete" and result["report"]["evaluation_verified"]
        held = {Path(v["name"]).stem for v in result["report"]["evaluation_images"]}
        sparse = work / "sfm/undistorted/sparse"
        model = pycolmap.Reconstruction(sparse)
        images = sorted(model.images.values(), key=lambda v: v.name)
        assert held <= {Path(im.name).stem for im in images}
        count = 0
        for im in images:
            if Path(im.name).stem in held:
                continue
            path = work / "sfm/undistorted/images" / im.name
            camera = model.cameras[im.camera_id]
            with Image.open(path) as photo:
                assert photo.size == (camera.width, camera.height)
            pose = np.asarray(im.cam_from_world().matrix())
            xy, z = [], []
            for obs in im.points2D:
                if not obs.has_point3D():
                    continue
                point = model.points3D[obs.point3D_id]
                if point.track.length() < 2 or not np.isfinite(point.error) or point.error >= 4:
                    continue
                camera_z = camera_depth(pose, point.xyz)
                if camera_z > 0 and np.isfinite(camera_z):
                    xy.append(obs.xy.tolist())
                    z.append(camera_z)
            records.append({"job": work.name, "name": im.name, "image": str(path),
                            "image_sha256": digest(path), "width": camera.width, "height": camera.height,
                            "camera_model": camera.model.name, "camera_params": camera.params.tolist(),
                            "cam_from_world": pose.tolist(), "xy": xy, "z": z})
            count += 1
        assert count == result["report"]["training_views"]
    save(args.output / "anchors.json", {"pycolmap": pycolmap.__version__, "views": records})
    print(f"Exported {len(records)} training views; evaluation views excluded.")


def infer(args):
    # Do not import PyCOLMAP here, including through another project module.
    import cv2
    import torch
    import torchvision

    started = time.monotonic()
    if args.device == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("MPS unavailable; refusing an implicit CPU fallback")
    args.output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(args.repository.resolve()))
    from depth_anything_v2.dpt import DepthAnythingV2

    torch.set_num_threads(4)
    model = DepthAnythingV2(encoder="vits", features=64, out_channels=[48, 96, 192, 384])
    model.load_state_dict(torch.load(args.weights, map_location="cpu", weights_only=True))
    model = model.to(args.device).eval()
    def sync():
        if args.device == "mps":
            torch.mps.synchronize()
    sync()
    result = {"status": "running", "torch": torch.__version__, "torchvision": torchvision.__version__, "opencv": cv2.__version__,
              "device": args.device, "precision": "float32", "input_size": args.input_size,
              "weights_sha256": digest(args.weights), "anchor_sha256": digest(args.anchors),
              "code_revision": subprocess.check_output(["git", "-C", str(args.repository), "rev-parse", "HEAD"], text=True).strip(),
              "dpt_sha256": digest(args.repository / "depth_anything_v2/dpt.py"),
              "model_parameters": sum(p.numel() for p in model.parameters()),
              "load_seconds": time.monotonic() - started, "views": []}
    try:
        for i, row in enumerate(json.loads(args.anchors.read_text())["views"]):
            assert digest(row["image"]) == row["image_sha256"]
            raw = cv2.imread(row["image"])
            assert raw is not None and raw.shape[:2] == (row["height"], row["width"])
            sync()
            start = time.monotonic()
            with torch.inference_mode():
                # Reuse upstream preprocessing; explicitly move its tensor to the requested device.
                tensor, (h, w) = model.image2tensor(raw, args.input_size)
                depth = model(tensor.to(args.device))
                depth = torch.nn.functional.interpolate(depth[:, None], (h, w), mode="bilinear", align_corners=True)[0, 0]
                output = depth.cpu().numpy()
            sync()
            seconds = time.monotonic() - start
            assert output.shape == raw.shape[:2] and np.isfinite(output).all()
            name = f"{row['job']}-{Path(row['name']).stem}.npy"
            np.save(args.output / name, output, allow_pickle=False)
            record = {k: row[k] for k in ("job", "name", "image_sha256")}
            record.update(prediction=name, prediction_sha256=digest(args.output / name), seconds=seconds,
                          min=float(output.min()), max=float(output.max()), first_in_process=i == 0,
                          network_dimensions=list(tensor.shape[-2:]), output_bytes=output.nbytes)
            if args.device == "mps":
                record["mps_driver_allocated_bytes_after_view"] = torch.mps.driver_allocated_memory()
            result["views"].append(record)
            print(row["job"], row["name"][:4], f"{seconds:.3f}s", flush=True)
        result["status"] = "complete"
    except BaseException as error:
        result["status"] = "failed"
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        result["total_seconds"] = time.monotonic() - started
        save(args.output / "inference.json", result)


def analyze(args):
    anchor_data = json.loads(args.anchors.read_text())
    inference = json.loads((args.predictions / "inference.json").read_text())
    assert inference["status"] == "complete" and inference["anchor_sha256"] == digest(args.anchors)
    by_key = {(r["job"], r["name"]): r for r in inference["views"]}
    assert len(by_key) == len(inference["views"]) == len(anchor_data["views"])
    rows = []
    for view in anchor_data["views"]:
        pred = by_key[(view["job"], view["name"])]
        assert view["image_sha256"] == pred["image_sha256"]
        path = args.predictions / pred["prediction"]
        assert digest(path) == pred["prediction_sha256"]
        depth = np.load(path, allow_pickle=False)
        assert depth.shape == (view["height"], view["width"])
        rows.append({"job": view["job"], "name": view["name"],
                     **analyze_view(depth, view["xy"], view["z"])})
    save(args.output, {"gate": GATE, "scope": "spatially held-out sparse SfM anchors on training images; not independent geometry ground truth",
                       "anchor_sha256": digest(args.anchors), "inference": inference, "views": rows})
    print(f"{sum(v['passed'] for v in rows)}/{len(rows)} views pass the sparse screening gate.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="stage", required=True)
    a = subs.add_parser("anchors")
    a.add_argument("--jobs", type=Path, nargs="+", required=True)
    a.add_argument("--output", type=Path, required=True)
    i = subs.add_parser("infer")
    i.add_argument("--anchors", type=Path, required=True)
    i.add_argument("--repository", type=Path, required=True)
    i.add_argument("--weights", type=Path, required=True)
    i.add_argument("--device", choices=("mps", "cpu"), required=True)
    i.add_argument("--input-size", type=int, default=518)
    i.add_argument("--output", type=Path, required=True)
    a = subs.add_parser("analyze")
    a.add_argument("--anchors", type=Path, required=True)
    a.add_argument("--predictions", type=Path, required=True)
    a.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    {"anchors": anchors, "infer": infer, "analyze": analyze}[args.stage](args)


if __name__ == "__main__":
    main()
