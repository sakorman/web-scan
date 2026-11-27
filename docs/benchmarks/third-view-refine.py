"""Compare two-view seeds with one checked training observation under fixed cameras."""
import argparse
import json
import os
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import pycolmap


def triangulate(views, observations):
    rows = []
    for index, xy in observations:
        p = views[index]["projection"]
        rows.extend([xy[0] * p[2] - p[0], xy[1] * p[2] - p[1]])
    h = np.linalg.svd(rows, full_matrices=False)[2][-1]
    if abs(h[3]) < 1e-9:
        return None
    xyz = h[:3] / h[3]
    errors = []
    for index, xy in observations:
        u, v, z = views[index]["projection"] @ np.r_[xyz, 1.]
        if z <= 0:
            return None
        errors.append(np.linalg.norm([u / z - xy[0], v / z - xy[1]]))
    if max(errors) > 1.5:
        return None
    return xyz, float(max(errors))


def save_model(out, source, rec, views, points, use_third):
    out.mkdir()
    (out / "images").mkdir()
    sparse = out / "sparse"
    sparse.mkdir()
    observations = defaultdict(list)
    with (sparse / "points3D.txt").open("w") as stream:
        for pid, seed in enumerate(points, 1):
            xyz, error, track = seed["three"] if use_third else seed["two"]
            row = [pid, *xyz, *seed["color"], error]
            for index, xy in track:
                row.extend([index + 1, len(observations[index])])
                observations[index].append((*xy, pid))
            stream.write(" ".join(map(str, row)) + "\n")
    with (sparse / "cameras.txt").open("w") as stream:
        for cid, cam in sorted(rec.cameras.items()):
            row = [cid, cam.model.name, cam.width, cam.height, *cam.params]
            stream.write(" ".join(map(str, row)) + "\n")
    with (sparse / "images.txt").open("w") as stream:
        for index, view in enumerate(views):
            im = view["image"]
            os.link(source / "images" / im.name, out / "images" / im.name)
            pose = im.cam_from_world()
            qx, qy, qz, qw = pose.rotation.quat
            row = [index + 1, qw, qx, qy, qz, *pose.translation, im.camera_id, im.name]
            stream.write(" ".join(map(str, row)) + "\n")
            stream.write(" ".join(str(x) for obs in observations[index] for x in obs) + "\n")


def run(source, output):
    started = time.monotonic()
    output.mkdir()
    rec = pycolmap.Reconstruction(source / "sparse")
    views = []
    cv2.setNumThreads(4)
    indices = {}
    for i, im in enumerate(sorted(rec.images.values(), key=lambda x: x.name)):
        indices[im.image_id] = i
        cam = rec.cameras[im.camera_id]
        if cam.model.name not in {"PINHOLE", "SIMPLE_PINHOLE"}:
            raise ValueError("Requires undistorted cameras")
        view = {"image": im, "projection": cam.calibration_matrix() @ im.cam_from_world().matrix()}
        if i % 8:
            rgb = cv2.imread(str(source / "images" / im.name))
            scale = min(1., 768 / max(cam.width, cam.height))
            size = (round(cam.width * scale), round(cam.height * scale))
            gray = cv2.resize(cv2.cvtColor(rgb, cv2.COLOR_BGR2GRAY), size, interpolation=cv2.INTER_AREA)
            view.update(gray=gray, rgb=rgb, scale=np.array(size) / [cam.width, cam.height])
        views.append(view)
    width = max(v["gray"].shape[1] for v in views if "gray" in v)
    height = max(v["gray"].shape[0] for v in views if "gray" in v)
    for view in views:
        if "gray" in view:
            h, w = view["gray"].shape
            view["limit"] = np.array([w, h]) - 8
            view["gray"] = cv2.copyMakeBorder(view["gray"], 0, height-h, 0, width-w, cv2.BORDER_REPLICATE)
    points = []
    for _, point in sorted(rec.points3D.items()):
        track = []
        for e in point.track.elements:
            i = indices[e.image_id]
            if i % 8:
                xy = views[i]["image"].points2D[e.point2D_idx].xy.copy()
                x, y = np.rint(xy - .5).astype(int)
                height, width = views[i]["rgb"].shape[:2]
                if 0 <= x < width and 0 <= y < height:
                    track.append((i, xy))
        if len(track) < 2:
            continue
        track.sort(key=lambda v: v[0])
        track = track[:2]
        result = triangulate(views, track)
        if result is None:
            continue
        xyz, error = result
        rays = [xyz - views[i]["image"].projection_center() for i, _ in track]
        cosine = rays[0] @ rays[1] / (np.linalg.norm(rays[0]) * np.linalg.norm(rays[1]))
        if cosine > np.cos(np.deg2rad(1.5)):
            continue
        i, xy = track[0]
        x, y = np.rint(xy - .5).astype(int)
        color = views[i]["rgb"][y, x, ::-1].copy()
        points.append(dict(two=(xyz, error, track), three=(xyz, error, track), color=color))
    baseline_seconds = time.monotonic() - started
    refinement_start = time.monotonic()
    pairs = defaultdict(list)
    for pid, seed in enumerate(points):
        xyz, _, ((i, xy), (j, _)) = seed["two"]
        for k in sorted(range(max(0, i - 3), min(len(views), i + 4)), key=lambda n: (abs(n-i), n)):
            if k in (i, j) or k % 8 == 0:
                continue
            u, v, z = views[k]["projection"] @ np.r_[xyz, 1.]
            if z <= 0:
                continue
            pred = np.array([u, v]) / z * views[k]["scale"] - .5
            start = xy * views[i]["scale"] - .5
            if (np.all(np.minimum(pred, start) >= 7) and np.all(pred <= views[k]["limit"])
                    and np.all(start <= views[i]["limit"])):
                pairs[i, k].append((pid, start, pred))
                break
    updated = 0
    for (i, k), group in pairs.items():
        prev, nxt = views[i]["gray"], views[k]["gray"]
        a = np.float32([r[1] for r in group]).reshape(-1, 1, 2)
        pred = np.float32([r[2] for r in group]).reshape(-1, 1, 2)
        options = dict(winSize=(15,15), maxLevel=2, flags=cv2.OPTFLOW_USE_INITIAL_FLOW)
        b, good, cost = cv2.calcOpticalFlowPyrLK(prev, nxt, a, pred.copy(), **options)
        back, reverse, _ = cv2.calcOpticalFlowPyrLK(nxt, prev, b, a.copy(), **options)
        keep = good.ravel().astype(bool) & reverse.ravel().astype(bool) & (cost.ravel() < 16)
        keep &= (np.linalg.norm((back-a)[:,0], axis=1) <= .5)
        keep &= (np.linalg.norm((b-pred)[:,0], axis=1) <= 4.)
        for n in np.flatnonzero(keep):
            seed = points[group[n][0]]
            xy = (b[n,0] + .5) / views[k]["scale"]
            track = [*seed["two"][2], (k, xy)]
            result = triangulate(views, track)
            if result is not None:
                seed["three"] = (*result, track)
                updated += 1
    elapsed = time.monotonic() - refinement_start
    if not points:
        raise ValueError("No qualifying two-view seeds")
    for label in ("two", "three"):
        save_model(output / label, source, rec, views, points, label == "three")
        model = pycolmap.Reconstruction(output / label / "sparse")
        assert model.num_points3D() == len(points) and model.num_images() == len(views)
    report = dict(frames=len(views), points=len(points), tested=sum(map(len, pairs.values())),
                  updated=updated, baseline_prep_seconds=baseline_seconds, extra_refine_seconds=elapsed)
    (output / "geometry.json").write_text(json.dumps(report, indent=2) + "\n")
    print(output, report, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    run(args.source, args.output)
