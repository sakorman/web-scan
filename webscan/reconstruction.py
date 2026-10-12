from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def normalize_scene(points, views):
    center = np.median(points, axis=0)
    radius = float(np.percentile(np.linalg.norm(points - center, axis=1), 90))
    radius = max(radius, 1e-4)
    for view in views:
        matrix = np.array(view["view"], dtype=np.float32)
        matrix[:3, 3] = (matrix[:3, :3] @ center + matrix[:3, 3]) / radius
        view["view"] = matrix.tolist()
    return (points - center) / radius, {
        "center": center.tolist(),
        "scale": radius,
        "units": "relative",
    }


def estimate_scene(images: Path, work: Path, progress, camera_mode="auto"):
    import pycolmap

    work.mkdir(parents=True, exist_ok=True)
    db = work / "database.db"
    progress("sfm", 0.08, "提取照片特征")
    # Independent camera parameters tolerate imported photos with different dimensions.
    pycolmap.extract_features(
        database_path=db,
        image_path=images,
        camera_mode=pycolmap.CameraMode.SINGLE
        if camera_mode == "shared"
        else pycolmap.CameraMode.AUTO,
        device=pycolmap.Device.cpu,
        extraction_options={
            "max_image_size": 1600,
            "sift": {"max_num_features": 8192},
            "num_threads": 4,
        },
    )
    progress("sfm", 0.17, "匹配视角与几何验证")
    pycolmap.match_exhaustive(
        database_path=db,
        device=pycolmap.Device.cpu,
        matching_options={"num_threads": 4, "guided_matching": True},
    )
    sparse = work / "sparse"
    sparse.mkdir(exist_ok=True)
    progress("sfm", 0.26, "恢复相机位姿与稀疏三维结构")
    maps = pycolmap.incremental_mapping(
        database_path=db,
        image_path=images,
        output_path=sparse,
        options={"num_threads": 4, "min_model_size": 3},
    )
    if not maps:
        raise RuntimeError(
            "照片之间没有建立可靠的三维关联。请增加重叠视角、平移拍摄，并避免反光和纯色表面。"
        )
    best = max(maps.values(), key=lambda m: m.num_reg_images())
    total = len(list(images.glob("*.jpg")))
    if best.num_reg_images() < 3 or best.num_reg_images() / total < 0.5:
        raise RuntimeError(
            f"仅 {best.num_reg_images()}/{total} 张照片成功定位；请补拍相邻视角。"
        )
    chosen = work / "chosen"
    chosen.mkdir(exist_ok=True)
    best.write(chosen)
    undistorted = work / "undistorted"
    progress("sfm", 0.36, "去畸变并准备训练照片")
    pycolmap.undistort_images(
        output_path=undistorted,
        input_path=chosen,
        image_path=images,
        undistort_options={"max_image_size": 1600},
    )
    result = pycolmap.Reconstruction(undistorted / "sparse")
    points = [
        p for p in result.points3D.values() if p.track.length() >= 2 and p.error < 4
    ]
    if len(points) < 12:
        raise RuntimeError("有效三维点不足，请围绕目标缓慢移动并增加纹理与光照。")
    xyz = np.array([p.xyz for p in points], dtype=np.float32)
    rgb = np.array([p.color for p in points], dtype=np.float32) / 255
    views = []
    for image in sorted(result.images.values(), key=lambda image: image.name):
        transform = image.cam_from_world()
        matrix = np.eye(4, dtype=np.float32)
        matrix[:3] = transform.matrix()
        cam = result.cameras[image.camera_id]
        views.append(
            {
                "image": str(undistorted / "images" / image.name),
                "view": matrix.tolist(),
                "K": cam.calibration_matrix().tolist(),
                "width": cam.width,
                "height": cam.height,
                "image_name": image.name,
            }
        )
    xyz, transform = normalize_scene(xyz, views)
    return {
        "points": xyz,
        "colors": rgb,
        "views": views,
        "normalization": transform,
        "registered_images": len(views),
        "input_images": total,
        "pose_source": "COLMAP",
        "camera_mode": camera_mode,
        "sparse_points": best.num_points3D(),
        "reprojection_error": best.compute_mean_reprojection_error(),
        "unregistered_images": sorted(
            set(p.name for p in images.glob("*.jpg"))
            - {image.name for image in best.images.values()}
        ),
    }


def look_at(position):
    position = np.asarray(position, dtype=np.float32)
    forward = -position / np.linalg.norm(position)
    right = np.cross(forward, np.array([0.0, -1.0, 0.0], dtype=np.float32))
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    matrix = np.eye(4, dtype=np.float32)
    matrix[:3, :3] = np.stack([right, down, forward])
    matrix[:3, 3] = -matrix[:3, :3] @ position
    return matrix


def create_fixture(path: Path):
    """Generate original calibrated Gaussian observations; never masquerades as photo SfM."""
    import torch
    from PIL import Image
    from .gaussian import render_reference, logit

    torch.set_num_threads(4)
    path.mkdir(parents=True, exist_ok=True)
    points, colors = [], []
    for ring in range(24):
        a = ring * 2 * np.pi / 24
        for tube in range(8):
            b = tube * 2 * np.pi / 8
            points.append(
                [
                    (0.65 + 0.22 * np.cos(b)) * np.cos(a),
                    0.22 * np.sin(b),
                    (0.65 + 0.22 * np.cos(b)) * np.sin(a),
                ]
            )
            colors.append(
                [
                    0.15 + 0.3 * (1 + np.sin(a)) / 2,
                    0.55 + 0.4 * (1 + np.cos(b)) / 2,
                    0.6 + 0.35 * (1 + np.cos(a)) / 2,
                ]
            )
    xyz, rgb = np.array(points, np.float32), np.array(colors, np.float32)
    means = torch.from_numpy(xyz)
    scales = torch.full((len(xyz), 3), np.log(0.082))
    quats = torch.tensor([[1.0, 0, 0, 0]]).repeat(len(xyz), 1)
    opacity, color = logit(torch.full((len(xyz),), 0.85)), logit(rgb)
    K = np.array([[135, 0, 64], [0, 135, 64], [0, 0, 1]], np.float32)
    views = []
    for i in range(10):
        angle = i * 2 * np.pi / 10
        view = look_at([2.8 * np.sin(angle), -1.8, 2.8 * np.cos(angle)])
        with torch.no_grad():
            image = render_reference(
                means,
                scales,
                quats,
                opacity,
                color,
                torch.from_numpy(view),
                torch.from_numpy(K),
                128,
                128,
            )
        name = f"fixture-{i:03}.jpg"
        Image.fromarray((image.numpy() * 255).astype("uint8")).save(
            path / name, quality=95
        )
        views.append({"name": name, "view": view.tolist(), "K": K.tolist()})
    rng = np.random.default_rng(2)
    return {
        "points": (xyz + rng.normal(0, 0.012, xyz.shape)).tolist(),
        "colors": np.clip(rgb * 0.85, 0, 1).tolist(),
        "views": views,
        "pose_source": "calibrated synthetic fixture",
        "units": "relative",
    }


def fixture_scene(images, metadata):
    source = json.loads(metadata.read_text())
    views = [{**v, "image": str(images / v["name"])} for v in source["views"]]
    return {
        **source,
        "points": np.array(source["points"], np.float32),
        "colors": np.array(source["colors"], np.float32),
        "views": views,
        "registered_images": len(views),
        "input_images": len(views),
        "normalization": {"center": [0, 0, 0], "scale": 1, "units": "relative"},
    }
