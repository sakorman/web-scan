"""Measure RGB-D visual odometry and a frozen RGB-only model against TUM desk poses."""
import argparse
from functools import lru_cache
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import time

import numpy as np

from scripts.benchmark_sensor_depth import associate
from webscan.sfm_cache import file_sha256

PREFIX = "rgbd_dataset_freiburg1_desk/"
K = np.array([[525., 0., 319.5], [0., 525., 239.5], [0., 0., 1.]])


def rgb_sequence(data, job):
    folder = data / "jobs" / job / "input"
    with closing(sqlite3.connect((data / "state.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)) as db:
        frames = []
        for image in sorted(folder.glob("*.jpg")):
            frame_id = image.stem.split("-", 1)[1]
            row = db.execute("SELECT metadata FROM frames WHERE id=?", (frame_id,)).fetchone()
            if row is None:
                raise ValueError(f"No source timestamp for {image.name}")
            metadata = json.loads(row[0])
            if metadata["source"] != "TUM RGB-D freiburg1_desk":
                raise ValueError("Expected TUM desk RGB input")
            frames.append((float(Path(metadata["original_name"]).stem), image))
    if not frames or any(a[0] >= b[0] for a, b in zip(frames, frames[1:])):
        raise ValueError("Expected captures in increasing timestamp order")
    return frames


def frame_identity(frames):
    return [{"timestamp": t, "image": path.name, "sha256": file_sha256(path)}
            for t, path in frames]


def load_depths(archive, frames):
    from PIL import Image

    with tarfile.open(archive) as bundle:
        listed = bundle.extractfile(PREFIX + "depth.txt").read().decode().splitlines()
        pairs = [line.split() for line in listed if line.strip() and not line.startswith("#")]
        matched = associate([t for t, _ in frames], [float(t) for t, _ in pairs], .02)
        selected = {PREFIX + pairs[j][1]: i for i, (j, _) in matched.items()}
    depths = {}
    with tarfile.open(archive, "r|gz") as bundle:
        for entry in bundle:
            if entry.name in selected:
                with Image.open(io.BytesIO(bundle.extractfile(entry).read())) as img:
                    if img.size != (640, 480):
                        raise ValueError("Unexpected depth dimensions")
                    depths[selected[entry.name]] = np.asarray(img).copy()
    return depths


def flow_matches(previous, current):
    import cv2

    corners = cv2.goodFeaturesToTrack(previous, 1000, .008, 6)
    if corners is None:
        return np.empty((0, 2)), np.empty((0, 2))
    params = dict(winSize=(21, 21), maxLevel=3)
    tracked, ok, _ = cv2.calcOpticalFlowPyrLK(previous, current, corners, None, **params)
    backward, returned, _ = cv2.calcOpticalFlowPyrLK(current, previous, tracked, None, **params)
    origin = corners[:, 0]
    destination = tracked[:, 0]
    keep = ((ok.ravel() != 0) & (returned.ravel() != 0) &
            (np.linalg.norm(origin - backward[:, 0], axis=1) < 1) &
            (destination[:, 0] >= 0) & (destination[:, 0] < current.shape[1]) &
            (destination[:, 1] >= 0) & (destination[:, 1] < current.shape[0]))
    return origin[keep], destination[keep]


def solve_relative(origin, destination, depth):
    import cv2

    height, width = depth.shape
    x, y = np.rint(origin).astype(int).T
    valid = (x >= 0) & (x < width) & (y >= 0) & (y < height)
    origin, destination = origin[valid], destination[valid]
    z = depth[y[valid], x[valid]].astype(float) / 5000
    use = (z > .25) & (z < 5)
    origin, destination, z = origin[use], destination[use], z[use]
    if len(z) < 12:
        return None, 0
    points = np.column_stack(((origin[:, 0] - K[0, 2]) * z / K[0, 0],
                              (origin[:, 1] - K[1, 2]) * z / K[1, 1], z))
    success, rotation, offset, inliers = cv2.solvePnPRansac(
        points, destination, K, None, iterationsCount=150, reprojectionError=2.5,
        confidence=.999, flags=cv2.SOLVEPNP_EPNP)
    if not success or inliers is None or len(inliers) < 12:
        return None, 0
    ids = inliers.ravel()
    rotation, offset = cv2.solvePnPRefineLM(points[ids], destination[ids], K, None, rotation, offset)
    transform = np.eye(4)
    transform[:3, :3] = cv2.Rodrigues(rotation)[0]
    transform[:3, 3] = offset.ravel()
    return transform, len(ids)


def odometry(frames, depths, mode):
    import cv2

    cv2.setNumThreads(4)
    cv2.setRNGSeed(42)

    @lru_cache(maxsize=12)
    def gray(index):
        image = cv2.imread(str(frames[index][1]), cv2.IMREAD_GRAYSCALE)
        if image is None or image.shape != (480, 640):
            raise ValueError(f"Invalid RGB frame {index}")
        return image

    @lru_cache(maxsize=12)
    def features(index):
        return cv2.SIFT_create(nfeatures=1200).detectAndCompute(gray(index), None)

    def register(source, target):
        a, b = flow_matches(gray(source), gray(target))
        transform, count = solve_relative(a, b, depths[source])
        method = "flow"
        if mode == "hybrid" and count < 50:
            ka, da = features(source)
            kb, db = features(target)
            if da is not None and db is not None and len(db) >= 2:
                matches = cv2.BFMatcher(cv2.NORM_L2).knnMatch(da, db, k=2)
                good = [m for m, n in matches if m.distance < .75 * n.distance]
                if len(good) >= 12:
                    a = np.array([ka[m.queryIdx].pt for m in good])
                    b = np.array([kb[m.trainIdx].pt for m in good])
                    alt, alt_count = solve_relative(a, b, depths[source])
                    if alt_count > count:
                        transform, count, method = alt, alt_count, "sift"
        links.append(dict(source=source, target=target, inliers=count, method=method))
        if transform is not None:
            poses[target] = poses[source] @ np.linalg.inv(transform)
            return True
        return False

    poses, links = {}, []
    anchor = None
    for i in range(len(frames)):
        if anchor is None:
            if i not in depths:
                continue
            poses[i] = np.eye(4)
        elif not register(anchor, i):
            continue
        if i in depths:
            anchor = i

    first_pass = sorted(poses)
    if mode != "forward":
        tried = {(link["source"], link["target"]) for link in links}
        while True:
            missing = set(range(len(frames))) - poses.keys()
            anchors = poses.keys() & depths.keys()
            pairs = sorted((abs(i - j), i, j) for i in missing for j in anchors
                           if 0 < abs(i - j) <= 5 and (j, i) not in tried)
            if not pairs:
                break
            for _, i, j in pairs:
                if i not in poses:
                    tried.add((j, i))
                    register(j, i)
    return poses, links, first_pass


def trajectory_score(predicted, reference, frames, scale_free):
    import pycolmap

    pairs = associate([stamp for stamp, _ in frames], reference[:, 0], .02)
    ids = sorted(predicted.keys() & pairs.keys())
    if len(ids) < 3:
        raise ValueError("Insufficient associated trajectory positions")
    gt = reference[[pairs[i][0] for i in ids]]
    centers = np.array([predicted[i][:3, 3] for i in ids])
    target = gt[:, 1:4]
    x, y = centers - centers.mean(axis=0), target - target.mean(axis=0)
    u, s, vt = np.linalg.svd(y.T @ x)
    sign = np.array([1., 1., np.linalg.det(u @ vt)])
    rotation = (u * sign) @ vt
    scale = (s @ sign) / (x ** 2).sum() if scale_free else 1.0
    dist = np.linalg.norm(scale * (x @ rotation.T) - y, axis=1)
    angles = []
    for index, row in zip(ids, gt):
        truth = pycolmap.Rotation3d(row[4:8]).matrix()
        delta = truth.T @ rotation @ predicted[index][:3, :3]
        angles.append(np.degrees(np.arccos(np.clip((np.trace(delta) - 1) / 2, -1, 1))))
    return dict(scored=len(ids), indices=ids, alignment_scale=scale,
                center_rmse_m=float(np.sqrt(np.mean(dist ** 2))),
                center_p90_m=float(np.percentile(dist, 90)),
                rotation_median_deg=float(np.median(angles)),
                rotation_p90_deg=float(np.percentile(angles, 90)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--job", required=True)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--mode", choices=("forward", "recover", "hybrid", "graph"), required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    frames = rgb_sequence(args.data, args.job)
    start = time.perf_counter()
    depths = load_depths(args.archive, frames)
    prep = time.perf_counter() - start
    timed = time.perf_counter()
    poses, links, initial = odometry(frames, depths, "hybrid" if args.mode == "graph" else args.mode)
    graph = None
    if args.mode == "graph":
        import cv2
        from scripts.rgbd_pose_graph import refine

        cv2.setRNGSeed(42)
        graph_started = time.perf_counter()
        poses, graph = refine(frames, depths, poses, links, solve_relative)
        graph["seconds"] = time.perf_counter() - graph_started
    seconds = time.perf_counter() - timed
    saved = {"frames": frame_identity(frames), "world_from_camera": {str(i): pose.tolist() for i, pose in poses.items()}}
    (args.out / "poses.json").write_text(json.dumps(saved))
    # Independent scoring begins only after the estimated camera trajectory is saved.
    import pycolmap

    with tarfile.open(args.archive) as bundle:
        gt = np.loadtxt(bundle.extractfile(PREFIX + "groundtruth.txt"))
    model = pycolmap.Reconstruction(args.data / "jobs" / args.job / "sfm/chosen")
    by_name = {im.name: im.cam_from_world().inverse().matrix() for im in model.images.values()}
    sfm = {i: by_name[path.name] for i, (_, path) in enumerate(frames) if path.name in by_name}
    common = poses.keys() & sfm.keys()
    result = dict(
        mode=args.mode, initial_indices=initial, input=len(frames), depth=len(depths),
        rgbd_poses=len(poses), sfm_poses=len(sfm),
        rgbd_missing=sorted(set(range(len(frames))) - poses.keys()),
        depth_io_seconds=prep, rgbd_odometry_seconds=seconds, links=links,
        rgbd=trajectory_score({i: poses[i] for i in common}, gt, frames, False),
        rgb_only=trajectory_score({i: sfm[i] for i in common}, gt, frames, True),
    )
    if graph is not None:
        result["graph"] = graph
    (args.out / "results.json").write_text(json.dumps(result, indent=2))
    for key in ("rgbd", "rgb_only"):
        print(key, {k: v for k, v in result[key].items() if k != "indices"})
    print("RGB-D", len(poses), "/", len(frames), round(seconds, 3), "seconds")


if __name__ == "__main__":
    main()
