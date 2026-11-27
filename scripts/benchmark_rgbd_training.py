"""Train two Brush models on the same RGB-D frames, camera and training-depth pixels."""
import argparse
import json
import shutil
import time
from pathlib import Path

import numpy as np
from PIL import Image

from scripts.benchmark_rgbd_odometry import K, frame_identity, load_depths, rgb_sequence
from scripts.benchmark_sfm_matching import train_scene
from webscan.reconstruction import normalize_scene


def prepare(work, frames, depths, poses, indices, *, seed_poses=None):
    import pycolmap

    if seed_poses is None:
        seed_poses = poses
    root = work / "sfm/undistorted"
    sparse = root / "sparse"
    images = root / "images"
    sparse.mkdir(parents=True)
    images.mkdir()
    (sparse / "cameras.txt").write_text("1 PINHOLE 640 480 525 525 319.5 239.5\n")
    views, points, centers = [], [], []
    yy, xx = np.mgrid[20:480:40, 20:640:40]
    with (sparse / "images.txt").open("w") as cameras:
        for position, index in enumerate(indices):
            source = frames[index][1]
            image_id = position + 1
            image = images / source.name
            shutil.copyfile(source, image)
            world_from_camera = poses[index]
            cam_from_world = np.linalg.inv(world_from_camera)
            qx, qy, qz, qw = pycolmap.Rotation3d(cam_from_world[:3, :3]).quat
            extrinsics = [qw, qx, qy, qz, *cam_from_world[:3, 3]]
            cameras.write(f"{image_id} " + " ".join(map(str, extrinsics)) + f" 1 {source.name}\n")
            measurements = []
            if position % 8 != 0 and index in depths:
                z = depths[index][yy, xx] / 5000
                valid = (z > .25) & (z < 5)
                x, y, z = xx[valid], yy[valid], z[valid]
                local = np.column_stack(((x-K[0, 2])*z/K[0, 0], (y-K[1, 2])*z/K[1, 1], z))
                seed_pose = seed_poses[index]
                world = local @ seed_pose[:3, :3].T + seed_pose[:3, 3]
                with Image.open(image) as rgb:
                    colors = np.asarray(rgb.convert("RGB"))[y, x]
                for j, (center, color) in enumerate(zip(world, colors)):
                    pid = len(points) + 1
                    centers.append(center)
                    points.append(" ".join(map(str, (pid, *center, *color, 0, image_id, j))))
                    measurements.append(f"{x[j]} {y[j]} {pid}")
            cameras.write(" ".join(measurements) + "\n")
            views.append(dict(image=str(image.resolve()), view=cam_from_world.tolist(),
                              K=K.tolist(), width=640, height=480, image_name=image.name))
    (sparse / "points3D.txt").write_text("\n".join(points) + "\n")
    model = pycolmap.Reconstruction(sparse)
    if model.num_reg_images() != len(views) or model.num_points3D() != len(points):
        raise ValueError("RGB-D reconstruction did not round-trip through COLMAP")
    _, normalization = normalize_scene(np.array(centers), views)
    return dict(views=views, normalization=normalization), len(points)


def read_poses(path, identity):
    record = json.loads(path.read_text())
    if record["frames"] != identity:
        raise ValueError("Pose timestamps or RGB inputs differ from this reconstruction")
    poses = {int(i): np.array(m, dtype=float) for i, m in record["world_from_camera"].items()}
    for index, matrix in poses.items():
        if (not 0 <= index < len(identity) or matrix.shape != (4, 4)
                or not np.isfinite(matrix).all()
                or not np.allclose(matrix[3], [0, 0, 0, 1])
                or not np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=1e-6)
                or not np.isclose(np.linalg.det(matrix[:3, :3]), 1)):
            raise ValueError(f"Invalid metric pose at frame {index}")
    return poses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True, help="Estimated poses.json")
    parser.add_argument("--candidate", type=Path, required=True, help="Estimated poses.json")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=1000)
    parser.add_argument("--resolution", type=int, default=512)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    frames = rgb_sequence(args.data, args.job)
    depths = load_depths(args.archive, frames)
    identity = frame_identity(frames)
    models = [read_poses(path, identity) for path in (args.baseline, args.candidate)]
    shared = sorted(models[0].keys() & models[1].keys())
    if len(shared) < 8:
        raise ValueError("Too few shared estimated poses for a holdout")
    result = dict(indices=shared, training=[i for p, i in enumerate(shared) if p % 8],
                  holdout=shared[::8], steps=args.steps, resolution=args.resolution)
    for name, poses in zip(("baseline", "candidate"), models):
        work = (args.out / name).resolve()
        start = time.monotonic()
        scene, seeds = prepare(work, frames, depths, poses, shared)
        seconds = time.monotonic() - start
        report = train_scene(scene, work, steps=args.steps, resolution=args.resolution)
        result[name] = dict(depth_seeds=seeds, preparation_seconds=seconds, **report)
        (args.out / "results.json").write_text(json.dumps(result, indent=2))
        print(name, result[name], flush=True)


if __name__ == "__main__":
    main()
