from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import numpy as np


AUTO_SHARED_RETRY_COVERAGE = 0.9
MAX_SHARED_RETRY_REPROJECTION_RATIO = 1.1


def match_images(database, *, mode="exhaustive", sequential_overlap=10):
    """Run the chosen COLMAP pairing strategy with the project's fixed checks.

    Sequential matching is intentionally opt-in here.  Its input order is part
    of the reconstruction contract, while ordinary photo uploads may be a
    gallery assembled in an arbitrary order.
    """
    if mode not in {"exhaustive", "sequential"}:
        raise ValueError(f"不支持的匹配方式：{mode}")
    if mode == "sequential" and (
        not isinstance(sequential_overlap, int) or not 1 <= sequential_overlap <= 50
    ):
        raise ValueError("顺序匹配重叠范围需为 1–50")

    import pycolmap

    matching_options = {"num_threads": 4, "guided_matching": True}
    verification_options = {"ransac": {"random_seed": 42}}
    if mode == "exhaustive":
        pycolmap.match_exhaustive(
            database_path=database,
            device=pycolmap.Device.cpu,
            matching_options=matching_options,
            verification_options=verification_options,
        )
        return {"mode": mode, "ransac_seed": 42}
    if mode == "sequential":
        pairing_options = pycolmap.SequentialPairingOptions(
            overlap=sequential_overlap,
            quadratic_overlap=True,
            loop_detection=False,
            num_threads=4,
        )
        pycolmap.match_sequential(
            database_path=database,
            device=pycolmap.Device.cpu,
            matching_options=matching_options,
            pairing_options=pairing_options,
            verification_options=verification_options,
        )
        return {
            "mode": mode,
            "overlap": sequential_overlap,
            "quadratic_overlap": True,
            "loop_detection": False,
            "ransac_seed": 42,
        }


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


def shared_retry_reason(camera_mode, image_sizes, attempt):
    """Return why an automatic shared-intrinsics retry is or is not safe."""
    if camera_mode != "auto":
        return "manual_camera_mode"
    if len(set(image_sizes)) != 1:
        return "mixed_image_dimensions"
    coverage = attempt["registered_images"] / max(attempt["input_images"], 1)
    if coverage >= AUTO_SHARED_RETRY_COVERAGE:
        return "auto_coverage_sufficient"
    return None


def prefer_shared_attempt(primary, candidate):
    """Keep the automatic camera model unless shared intrinsics clearly help."""
    primary_error = primary.get("reprojection_error")
    candidate_error = candidate.get("reprojection_error")
    return (
        candidate["registered_images"] > primary["registered_images"]
        and candidate["sparse_points"] > primary["sparse_points"]
        and primary_error is not None
        and candidate_error is not None
        and candidate_error
        <= primary_error * MAX_SHARED_RETRY_REPROJECTION_RATIO
    )


def _image_sizes(image_paths):
    from PIL import Image

    sizes = []
    for path in image_paths:
        with Image.open(path) as image:
            sizes.append(image.size)
    return sizes


def _map_scene(
    images,
    work,
    progress,
    image_names,
    *,
    camera_mode,
    matching_mode,
    sequential_overlap,
):
    """Build one sparse COLMAP model without committing it as the chosen model."""
    import pycolmap

    started = time.monotonic()
    work.mkdir(parents=True, exist_ok=True)
    db = work / "database.db"
    mode = (
        pycolmap.CameraMode.SINGLE
        if camera_mode == "shared"
        else pycolmap.CameraMode.AUTO
    )
    with pycolmap.Database.open(db):
        pass
    pycolmap.import_images(
        database_path=db,
        image_path=images,
        camera_mode=mode,
        image_names=image_names,
        options={"camera_model": "SIMPLE_RADIAL"},
    )
    progress("sfm", 0.08, "提取照片特征")
    pycolmap.extract_features(
        database_path=db,
        image_path=images,
        camera_mode=mode,
        image_names=image_names,
        device=pycolmap.Device.cpu,
        extraction_options={
            "max_image_size": 1600,
            "sift": {"max_num_features": 8192},
            "num_threads": 4,
        },
    )
    progress("sfm", 0.17, "匹配视角与几何验证")
    matching = match_images(
        db, mode=matching_mode, sequential_overlap=sequential_overlap
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
        return {
            "camera_mode": camera_mode,
            "matching": matching,
            "model": None,
            "summary": {
                "input_images": len(image_names),
                "registered_images": 0,
                "sparse_points": 0,
                "reprojection_error": None,
                "unregistered_images": image_names,
                "seconds": round(time.monotonic() - started, 4),
            },
        }
    best = max(maps.values(), key=lambda model: model.num_reg_images())
    registered = {image.name for image in best.images.values()}
    summary = {
        "input_images": len(image_names),
        "registered_images": best.num_reg_images(),
        "sparse_points": best.num_points3D(),
        "reprojection_error": best.compute_mean_reprojection_error(),
        "unregistered_images": sorted(set(image_names) - registered),
        "seconds": round(time.monotonic() - started, 4),
    }
    return {
        "camera_mode": camera_mode,
        "matching": matching,
        "model": best,
        "summary": summary,
    }


def estimate_scene(
    images: Path,
    work: Path,
    progress,
    camera_mode="auto",
    matching_mode="exhaustive",
    sequential_overlap=10,
):
    import pycolmap

    if camera_mode not in {"auto", "shared"}:
        raise ValueError(f"不支持的相机模式：{camera_mode}")
    image_paths = sorted(images.glob("*.jpg"))
    # Assign image IDs before parallel feature extraction can reorder writes.
    image_names = [path.name for path in image_paths]
    if len(image_names) < 3:
        raise RuntimeError("至少需要 3 张 JPEG 照片才能恢复三维场景。")

    primary = _map_scene(
        images,
        work,
        progress,
        image_names,
        camera_mode=camera_mode,
        matching_mode=matching_mode,
        sequential_overlap=sequential_overlap,
    )
    if primary["model"] is None:
        raise RuntimeError(
            "照片之间没有建立可靠的三维关联。请增加重叠视角、平移拍摄，并避免反光和纯色表面。"
        )
    selected = primary
    retry = {
        "attempted": False,
        "selected": False,
        "reason": shared_retry_reason(
            camera_mode, _image_sizes(image_paths), primary["summary"]
        ),
        "auto": primary["summary"],
    }
    if retry["reason"] is None:
        retry_root = work / "shared-retry"
        progress("sfm", 0.28, "自动定位不足，尝试同镜头内参")

        def retry_progress(stage, fraction, message):
            progress(stage, 0.28 + fraction * 0.08, f"同镜头重试：{message}")

        try:
            candidate = _map_scene(
                images,
                retry_root,
                retry_progress,
                image_names,
                camera_mode="shared",
                matching_mode=matching_mode,
                sequential_overlap=sequential_overlap,
            )
            retry.update(attempted=True, shared=candidate["summary"])
            if candidate["model"] is not None and prefer_shared_attempt(
                primary["summary"], candidate["summary"]
            ):
                selected = candidate
                retry.update(selected=True, reason="shared_model_selected")
            else:
                retry["reason"] = "shared_candidate_not_better"
        except RuntimeError as error:
            retry.update(
                attempted=True,
                reason="shared_retry_failed",
                shared={"error": str(error)[:240]},
            )
    best = selected["model"]
    summary = selected["summary"]
    total = summary["input_images"]
    if best.num_reg_images() < 3 or best.num_reg_images() / total < 0.5:
        raise RuntimeError(
            f"仅 {best.num_reg_images()}/{total} 张照片成功定位；请补拍相邻视角。"
        )
    chosen = work / "chosen"
    chosen.mkdir(exist_ok=True)
    best.write(chosen)
    retry_root = work / "shared-retry"
    if retry_root.exists():
        shutil.rmtree(retry_root)
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
        "camera_mode_effective": selected["camera_mode"],
        "camera_mode_retry": retry,
        "matching_seed": selected["matching"]["ransac_seed"],
        "matching": selected["matching"],
        "sparse_points": summary["sparse_points"],
        "reprojection_error": summary["reprojection_error"],
        "unregistered_images": summary["unregistered_images"],
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

    from .gaussian import logit, render_reference

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
