"""Cross camera extrinsics and depth-seed poses on the same Freiburg1 RGB frames."""
import argparse
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from scripts.benchmark_rgbd_odometry import frame_identity
from scripts.benchmark_rgbd_sequence import read_sequence
from scripts.benchmark_rgbd_training import prepare, read_poses
from scripts.benchmark_sfm_matching import digest_inputs, train_scene
from webscan.raw_evaluation import masked_metrics


def colmap_poses(sparse, frames):
    import pycolmap

    names = {path.name: i for i, (_, path) in enumerate(frames)}
    result = {}
    reconstruction = pycolmap.Reconstruction(sparse)
    for image in reconstruction.images.values():
        pose = np.eye(4)
        pose[:3] = image.cam_from_world().inverse().matrix()
        result[names[image.name]] = pose
    return result


def align_to_metric(poses, metric, train):
    import pycolmap

    src = np.array([poses[i][:3, 3] for i in train])
    dst = np.array([metric[i][:3, 3] for i in train])
    alignment = pycolmap.estimate_sim3d(src, dst)
    if alignment is None or alignment.scale <= 0:
        raise ValueError("Training camera centers do not define a valid similarity")
    aligned = {}
    for index, pose in poses.items():
        result = np.eye(4)
        result[:3, :3] = alignment.rotation.matrix() @ pose[:3, :3]
        result[:3, 3] = alignment * pose[:3, 3]
        aligned[index] = result
    return aligned, float(alignment.scale)


def raw_scores(directory, photos):
    if {p.stem for p in photos} != {p.stem for p in directory.glob("*.png")}:
        raise ValueError("Saved evaluation images do not match the expected held-out photos")
    scores = []
    for path in photos:
        with Image.open(directory / (path.stem + ".png")) as image:
            if image.size != (512, 384):
                raise ValueError("Expected a 512 × 384 render")
            render = np.asarray(image.convert("RGB")) / 255.
        with Image.open(path) as image:
            target = np.asarray(image.convert("RGB").resize((512, 384), Image.Resampling.LANCZOS)) / 255.
        metrics = masked_metrics(render, target, np.ones(render.shape[:2], dtype=bool))
        scores.append(dict(photo=path.name, psnr=metrics["psnr"], ssim=metrics["ssim"]))
    return dict(views=scores, psnr=np.mean([s["psnr"] for s in scores]),
                ssim=np.mean([s["ssim"] for s in scores]))


def run(args):
    args.out.mkdir(parents=True, exist_ok=False)
    sequence_dir = f"rgbd_dataset_{args.sequence}/"
    frames, depths = read_sequence(args.archive, sequence_dir, args.out / "input", 96)
    metric = read_poses(args.graph, frame_identity(frames))
    manifest = json.loads((args.colmap / "summary.json").read_text())
    if digest_inputs(args.out / "input") != manifest["inputs"]:
        raise ValueError("COLMAP received different RGB inputs")
    rgb = colmap_poses(args.colmap / "exhaustive/sfm/chosen", frames)
    shared = sorted(metric.keys() & rgb.keys())
    if len(shared) < 8:
        raise ValueError("Need at least eight common cameras for a holdout")
    holdout = shared[::8]
    train = [i for i in shared if i not in holdout]
    fitted, scale = align_to_metric(rgb, metric, train)
    choices = {"graph": metric, "colmap": fitted}
    report = dict(sequence=args.sequence, input=len(frames), registered=len(shared),
                  failed=sorted(set(range(len(frames))) - set(shared)), training=train,
                  holdout=holdout, scale=scale, steps=1000, resolution=512, runs=[])
    for camera, seed in [("colmap", "colmap"), ("graph", "colmap"),
                         ("colmap", "graph"), ("graph", "graph")]:
        work = (args.out / f"{camera}-{seed}").resolve()
        start = time.monotonic()
        scene, count = prepare(work, frames, depths, choices[camera], shared,
                               seed_poses=choices[seed])
        prep = time.monotonic() - start
        model = train_scene(scene, work, steps=1000, resolution=512)
        raw = raw_scores(work / "brush/eval_1000", [frames[i][1] for i in holdout])
        report["runs"].append(dict(camera=camera, seeds=seed, depth_points=count,
                                   preparation_seconds=prep, raw=raw, **model))
        (args.out / "results.json").write_text(json.dumps(report, indent=2) + "\n")
        print(camera, seed, raw["psnr"], raw["ssim"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--sequence", required=True, choices=("freiburg1_desk", "freiburg1_xyz"))
    parser.add_argument("--graph", required=True, type=Path, help="Estimated graph.json")
    parser.add_argument("--colmap", required=True, type=Path, help="Exhaustive matching benchmark root")
    parser.add_argument("--out", required=True, type=Path)
    run(parser.parse_args())
