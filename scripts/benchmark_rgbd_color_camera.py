"""Measure a fixed Freiburg1 color calibration on frozen RGB-D poses and depth seeds."""
import argparse
import json
import shutil
import time
from pathlib import Path

import cv2
import numpy as np
import pycolmap
from PIL import Image

from scripts.benchmark_sfm_matching import train_scene
from webscan.reconstruction import normalize_scene
from webscan.raw_evaluation import bilinear_sample, masked_metrics, raw_pixel_centers
from webscan.sfm_cache import file_sha256


SIZE = (640, 480)
SCORE_SIZE = (512, 384)
DEFAULT = np.array([[525., 0., 319.5], [0., 525., 239.5], [0., 0., 1.]])
COLOR = np.array([[517.3, 0., 318.6], [0., 516.5, 255.3], [0., 0., 1.]])
DISTORTION = np.array([.2624, -.9531, -.0054, .0026, 1.1633])


def maps():
    rectified, _ = cv2.getOptimalNewCameraMatrix(COLOR, DISTORTION, SIZE, 0)
    remap = cv2.initUndistortRectifyMap(COLOR, DISTORTION, None, rectified, SIZE, cv2.CV_32FC1)
    centers = raw_pixel_centers(*SIZE, *SCORE_SIZE) - .5
    normalized = cv2.undistortPoints(centers.reshape(-1, 1, 2), COLOR, DISTORTION, P=rectified)
    inverse = (normalized.reshape(*centers.shape) + .5) * (SCORE_SIZE[0] / SIZE[0]) - .5
    return rectified, remap, inverse


def prepare(source, work, calibrated, rectified, remap):
    root = work / "sfm/undistorted"
    images = root / "images"
    sparse = root / "sparse"
    images.mkdir(parents=True)
    sparse.mkdir()
    shutil.copyfile(source / "sparse/points3D.txt", sparse / "points3D.txt")
    src = pycolmap.Reconstruction(source / "sparse")
    if len(src.cameras) != 1 or not np.allclose(next(iter(src.cameras.values())).calibration_matrix(), DEFAULT):
        raise ValueError("Expected a single unchanged default Freiburg1 pinhole camera")
    k = rectified.copy() if calibrated else DEFAULT.copy()
    if calibrated:
        k[:2, 2] += .5  # OpenCV integer centers to COLMAP corner-based coordinates.
    (sparse / "cameras.txt").write_text(f"1 PINHOLE 640 480 {k[0,0]} {k[1,1]} {k[0,2]} {k[1,2]}\n")
    views = []
    with (sparse / "images.txt").open("w") as out:
        for image in sorted(src.images.values(), key=lambda im: im.name):
            src_path = source / "images" / image.name
            path = images / (src_path.stem + ".png")
            with Image.open(src_path) as rgb:
                if rgb.size != SIZE:
                    raise ValueError("Expected native Freiburg1 image size")
                pixels = np.asarray(rgb.convert("RGB"))
            if calibrated:
                pixels = cv2.remap(pixels, *remap, interpolation=cv2.INTER_LINEAR)
            Image.fromarray(pixels).save(path, compress_level=1)
            rigid = image.cam_from_world()
            qx, qy, qz, qw = rigid.rotation.quat
            fields = [image.image_id, qw, qx, qy, qz, *rigid.translation, 1, path.name]
            out.write(" ".join(map(str, fields)) + "\n")
            observations = image.points2D
            if observations:
                xy = np.array([point.xy for point in observations])
                if calibrated:
                    # Original measurements were recorded as zero-based sensor pixels.
                    xy = cv2.undistortPoints(xy[:, None], COLOR, DISTORTION, P=rectified)[:, 0] + .5
                triples = [f"{x} {y} {point.point3D_id}" for (x,y),point in zip(xy,observations)]
                out.write(" ".join(triples))
            out.write("\n")
            pose = np.eye(4)
            pose[:3] = rigid.matrix()
            views.append(dict(image=str(path.resolve()), image_name=path.name, view=pose.tolist(),
                              K=k.tolist(), width=640, height=480))
    centers = np.array([point.xyz for point in src.points3D.values()])
    _, normalization = normalize_scene(centers, views)
    return dict(views=views, normalization=normalization), src.num_points3D()


def score(source, out, inverse):
    photos = sorted((source / "images").glob("*.jpg"))[::8]
    scores = []
    for index, photo in enumerate(photos):
        name = photo.stem + ".png"
        with Image.open(photo) as im:
            target = np.asarray(im.convert("RGB").resize(SCORE_SIZE, Image.Resampling.LANCZOS)) / 255.
        row = dict(index=int(photo.name[:4]))
        with Image.open(out / "color/brush/eval_1000" / name) as im:
            warped, keep = bilinear_sample(np.asarray(im.convert("RGB")) / 255., inverse[:,:,0], inverse[:,:,1])
        with Image.open(out / "default/brush/eval_1000" / name) as im:
            original = np.asarray(im.convert("RGB")) / 255.
        for label, render in (("default", original), ("color", warped)):
            metrics = masked_metrics(render, target, keep)
            row[label] = {k: metrics[k] for k in ("psnr", "ssim", "valid_pixel_fraction")}
            if index in (0, 6):
                crop = np.where(keep[:,:,None], render, 0)
                Image.fromarray((np.clip(crop,0,1)*255).round().astype("uint8")).save(out / f"{row['index']}-{label}.png")
        scores.append(row)
    return scores


def run(source, out, order):
    out.mkdir(parents=True, exist_ok=False)
    rectified, remap, inverse = maps()
    result = dict(order=order, rectified_k_cv=rectified.tolist(),
                  points_sha256=file_sha256(source / "sparse/points3D.txt"), runs={})
    for label in order:
        work = (out / label).resolve()
        started = time.monotonic()
        scene, count = prepare(source, work, label == "color", rectified, remap)
        seconds = time.monotonic() - started
        report = train_scene(scene, work, steps=1000, resolution=512)
        result["runs"][label] = dict(preparation=seconds, initial_points=count, **report)
    result["views"] = score(source, out, inverse)
    for label in order:
        result["runs"][label]["raw"] = {
            key: float(np.mean([v[label][key] for v in result["views"]]))
            for key in ("psnr", "ssim", "valid_pixel_fraction")}
    (out / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(out.name, [(label, result["runs"][label]["raw"]) for label in order], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--color-first", action="store_true")
    args = parser.parse_args()
    run(args.source, args.out, ["color", "default"] if args.color_first else ["default", "color"])
