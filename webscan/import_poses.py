"""Import an undistorted, posed COLMAP capture into the local Web Scan studio."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .reconstruction import normalize_scene
from .sfm_cache import cache_key, file_sha256, publish_snapshot
from .store import Store, uid


def freeze_image(source, destination, camera):
    with Image.open(source) as image:
        if image.size != (camera.width, camera.height) or image.width * image.height > 48_000_000:
            raise ValueError(f"照片尺寸与相机不符或超过 4800 万像素：{source.name}")
        if image.format not in ("JPEG", "PNG") or image.getexif().get(274, 1) != 1:
            raise ValueError(f"请提供与相机像素坐标一致的正向 JPEG 或 PNG：{source.name}")
        image.load()
        if image.format == "JPEG" and image.mode == "RGB":
            shutil.copyfile(source, destination)
        else:
            image.convert("RGB").save(destination, format="JPEG", quality=95, subsampling=0)


def prepare(source, sparse, folder, sfm, sid):
    import pycolmap

    model = pycolmap.Reconstruction(sparse)
    registered = sorted((im for im in model.images.values() if im.has_pose), key=lambda im: im.name)
    if not 3 <= len(registered) <= 200:
        raise ValueError("导入需要 3–200 张已定位照片")
    if len(registered) != len(model.images):
        raise ValueError("稀疏模型含未定位照片，请先导出已定位子模型")
    cameras = {im.camera_id: model.cameras[im.camera_id] for im in registered}
    for camera in cameras.values():
        if camera.model.name not in ("PINHOLE", "SIMPLE_PINHOLE"):
            raise ValueError("请先用 COLMAP image_undistorter 导出去畸变照片和针孔相机")
        if not camera.verify_params() or not np.isfinite(camera.params).all() or min(camera.focal_length_x, camera.focal_length_y) <= 0:
            raise ValueError("针孔相机内参无效")
    points = list(model.points3D.values())
    if not points:
        raise ValueError("导入需要带颜色的三维初始点")
    xyz = np.array([point.xyz for point in points], dtype=np.float32)
    if not np.isfinite(xyz).all():
        raise ValueError("稀疏模型包含无效坐标")
    colors = np.array([point.color for point in points], dtype=np.float32) / 255
    assets = folder / "images"
    inputs = sfm / "undistorted/images"
    output_sparse = sfm / "undistorted/sparse"
    for directory in (assets, inputs, output_sparse):
        directory.mkdir(parents=True)
    photos = (source / "images").resolve(strict=True)
    keep_names = all(Path(im.name).name == im.name and Path(im.name).suffix.lower() in (".jpg", ".jpeg")
                     for im in registered)
    digest_set = set()
    frames, views = [], []
    total_bytes = 0
    for position, image in enumerate(registered):
        original_name = image.name
        path = (photos / original_name).resolve(strict=True)
        if Path(original_name).is_absolute() or not path.is_relative_to(photos):
            raise ValueError("模型引用的照片超出 images 目录")
        camera = cameras[image.camera_id]
        matrix = np.eye(4)
        matrix[:3] = image.cam_from_world().matrix()
        rotation = matrix[:3, :3]
        if (not np.isfinite(matrix).all() or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6)
                or not np.isclose(np.linalg.det(rotation), 1.0)):
            raise ValueError(f"相机外参无效：{original_name}")
        fid = uid()
        frozen = assets / f"{fid}.jpg"
        freeze_image(path, frozen, camera)
        sha = file_sha256(frozen)
        if sha in digest_set:
            raise ValueError(f"采集中包含内容重复的照片：{original_name}")
        digest_set.add(sha)
        size = frozen.stat().st_size
        total_bytes += size
        if total_bytes > 512 * 1024 * 1024:
            raise ValueError("照片总容量超过 512 MB")
        if not keep_names:
            image.name = f"{position:04d}-{fid}.jpg"
        frames.append(dict(id=fid, session_id=sid, sha=sha, image_name=image.name,
                           width=camera.width, height=camera.height,
                           bytes=size, metadata=dict(source="posed", original_name=original_name)))
        target = inputs / image.name
        os.link(frozen, target)
        views.append(dict(image=str(target), image_name=image.name, width=camera.width,
                          height=camera.height, view=matrix.tolist(), K=camera.calibration_matrix().tolist(),
                          original_camera=dict(model=camera.model.name, width=camera.width,
                                               height=camera.height, params=camera.params.tolist())))
    if keep_names:
        # Retain upstream ordering as well as geometry for the training loader.
        extension = "bin" if (sparse / "cameras.bin").is_file() else "txt"
        for stem in ("cameras", "images", "points3D", "rigs", "frames"):
            path = sparse / f"{stem}.{extension}"
            if path.is_file():
                shutil.copyfile(path, output_sparse / path.name)
    else:
        model.write(output_sparse)
    xyz, normalization = normalize_scene(xyz, views)
    scene = dict(points=xyz.tolist(), colors=colors.tolist(), views=views, normalization=normalization,
                 registered_images=len(views), input_images=len(views), sparse_points=len(points),
                 pose_source="导入的 COLMAP 格式位姿", camera_mode="provided", camera_mode_effective="provided",
                 camera_model=", ".join(sorted({c.model.name for c in cameras.values()})))
    (sfm / "scene.json").write_text(json.dumps(scene, allow_nan=False))
    manifest = dict(id=sid, frames=frames)
    sparse_identity = {path.name: file_sha256(path) for path in sorted(output_sparse.iterdir())}
    key = cache_key(manifest, {"provided_sparse": sparse_identity})
    publish_snapshot(sfm, folder / "posed", key)
    manifest["posed_key"] = key
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
    return frames


def import_capture(source, sparse, data, name):
    source = Path(source).resolve()
    if sparse is None:
        sparse = source / "sparse"
        if not any((sparse / f"cameras.{ext}").exists() for ext in ("bin", "txt")):
            models = [p for p in sparse.iterdir() if p.is_dir()
                      and any((p / f"cameras.{ext}").exists() for ext in ("bin", "txt"))]
            if len(models) != 1:
                raise ValueError("请用 --sparse 指定一份稀疏模型")
            sparse = models[0]
    name = name.strip()
    if not 1 <= len(name) <= 80:
        raise ValueError("采集名称为 1–80 个字符")
    store = Store(data)
    sid = uid()
    destination = store.session_dir(sid)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=store.root, prefix=".posed-") as scratch:
        root = Path(scratch)
        folder = root / "capture"
        frames = prepare(source, sparse, folder, root / "sfm", sid)
        published = False
        try:
            with store.transaction() as db:
                db.execute("INSERT INTO sessions(id,name,status,kind,created) VALUES(?,?,?,?,?)",
                           (sid, name, "ready", "posed", time.time()))
                db.executemany("INSERT INTO frames VALUES(?,?,?,?,?,?,?)", [
                    (f["id"], sid, f["sha"], f["width"], f["height"], f["bytes"],
                     json.dumps(f["metadata"], ensure_ascii=False)) for f in frames
                ])
                folder.rename(destination)
                published = True
        except BaseException:
            if published:
                destination.rename(folder)
            raise
    return sid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Directory with undistorted images/ and sparse/")
    parser.add_argument("--sparse", type=Path, help="Explicit sparse model directory")
    parser.add_argument("--data", type=Path, default=Path(".data"))
    parser.add_argument("--name", help="Studio capture name")
    args = parser.parse_args()
    sid = import_capture(args.source, args.sparse, args.data, args.name or args.source.resolve().name)
    print(sid)


if __name__ == "__main__":
    main()
