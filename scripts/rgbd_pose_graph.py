"""Refine metric RGB-D poses using verified matches across nonadjacent frames."""
from functools import lru_cache

import numpy as np


def propose_pairs(poses, depth_indices):
    pairs = set()
    for target, view in poses.items():
        options = []
        for source in depth_indices & poses.keys():
            if abs(source - target) < 8:
                continue
            distance = np.linalg.norm(poses[source][:3, 3] - view[:3, 3])
            facing = poses[source][:3, 2] @ view[:3, 2]
            if distance < .35 and facing > np.cos(np.deg2rad(35)):
                options.append((distance + .4 * (1 - facing), source))
        if options:
            source = min(options)[1]
            if (target, source) not in pairs:
                pairs.add((source, target))
    return sorted(pairs)


def align_graph(poses, edges):
    """Fit world-from-camera rotations, project to SO(3), then fit camera centers."""
    ids = sorted(poses)
    slot = {index: offset for offset, index in enumerate(ids)}
    anchor = next(iter(poses))
    rows, cols = len(edges) + 1, len(ids)
    rotations = np.zeros((3 * rows, 3 * cols))
    rotation_rhs = np.zeros((3 * rows, 3))
    centers = np.zeros((rows, cols))
    center_rhs = np.zeros((rows, 3))
    for row, (source, target, relative, support) in enumerate(edges):
        weight = np.sqrt(min(support, 80))
        i, j = 3 * slot[source], 3 * slot[target]
        block = slice(3 * row, 3 * row + 3)
        rotations[block, i:i + 3] = weight * np.eye(3)
        rotations[block, j:j + 3] = -weight * relative[:3, :3].T
        centers[row, slot[source]] = weight
        centers[row, slot[target]] = -weight

    # Fix the gauge at the first depth-bearing frame, including nonzero frame IDs.
    i = 3 * slot[anchor]
    rotations[-3:, i:i + 3] = 100 * np.eye(3)
    rotation_rhs[-3:] = 100 * poses[anchor][:3, :3].T
    solution = np.linalg.lstsq(rotations, rotation_rhs, rcond=None)[0]
    fitted = {}
    for index, block in zip(ids, solution.reshape(-1, 3, 3)):
        u, _, vt = np.linalg.svd(block.T)
        pose = np.eye(4)
        pose[:3, :3] = (u * [1, 1, np.linalg.det(u @ vt)]) @ vt
        fitted[index] = pose
    for row, (_, target, relative, support) in enumerate(edges):
        center_rhs[row] = np.sqrt(min(support, 80)) * (fitted[target][:3, :3] @ relative[:3, 3])
    centers[-1, slot[anchor]] = 100
    center_rhs[-1] = 100 * poses[anchor][:3, 3]
    for index, center in zip(ids, np.linalg.lstsq(centers, center_rhs, rcond=None)[0]):
        fitted[index][:3, 3] = center
    return fitted


def refine(frames, depths, poses, links, solve_relative):
    import cv2

    detector = cv2.SIFT_create(nfeatures=1200)

    @lru_cache(maxsize=96)
    def features(index):
        image = cv2.imread(str(frames[index][1]), cv2.IMREAD_GRAYSCALE)
        return detector.detectAndCompute(image, None)

    edges = []
    for link in links:
        if link["inliers"]:
            source, target = link["source"], link["target"]
            transform = np.linalg.inv(poses[target]) @ poses[source]
            edges.append((source, target, transform, link["inliers"]))

    pairs = propose_pairs(poses, depths.keys())
    accepted = []
    for source, target in pairs:
        a, da = features(source)
        b, db = features(target)
        if da is None or db is None or len(db) < 2:
            continue
        matches = cv2.BFMatcher().knnMatch(da, db, k=2)
        good = [m for m, n in matches if m.distance < .75 * n.distance]
        if len(good) < 35:
            continue
        origin = np.array([a[m.queryIdx].pt for m in good])
        destination = np.array([b[m.trainIdx].pt for m in good])
        relative, support = solve_relative(origin, destination, depths[source])
        if support < 35:
            continue
        delta = np.linalg.inv(relative) @ np.linalg.inv(poses[target]) @ poses[source]
        angle = np.degrees(np.arccos(np.clip((np.trace(delta[:3, :3]) - 1) / 2, -1, 1)))
        distance = float(np.linalg.norm(delta[:3, 3]))
        if distance <= .2 and angle <= 12:
            edges.append((source, target, relative, support))
            accepted.append(dict(source=source, target=target, support=support,
                                 angle_deg=angle, translation_m=distance))
    return (align_graph(poses, edges) if accepted else poses,
            dict(attempted=len(pairs), accepted=accepted))
