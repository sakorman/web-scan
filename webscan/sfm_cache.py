"""Verified local SfM snapshots, shared by training runs of one frozen capture.

Only the current single-worker runner publishes snapshots. A snapshot is visible
after all of its files and checksum manifest have been written successfully.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import tempfile
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pipeline_signature() -> dict:
    root = Path(__file__).parent
    return {
        "format": 1,
        "pycolmap": importlib.metadata.version("pycolmap"),
        "numpy": importlib.metadata.version("numpy"),
        "implementation": {
            name: file_sha256(root / name)
            for name in ("reconstruction.py", "sfm.py", "sfm_cache.py")
        },
    }


def cache_key(manifest: dict, signature: dict) -> str:
    # Frame order is part of camera registration; do not sort these observations.
    inputs = [
        {key: frame[key] for key in ("id", "sha", "width", "height")}
        for frame in manifest["frames"]
    ]
    encoded = json.dumps(
        {"inputs": inputs, "pipeline": signature}, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_inputs(source: Path, manifest: dict) -> None:
    for frame in manifest["frames"]:
        image = source / "images" / f"{frame['id']}.jpg"
        if file_sha256(image) != frame["sha"]:
            raise ValueError("采集文件校验失败，照片已发生变化；请重新采集。")


def verified_snapshot(root: Path, key: str) -> bool:
    try:
        descriptor = json.loads((root / "complete.json").read_text())
        if descriptor["key"] != key:
            return False
        records = descriptor["files"]
        actual = {
            file.relative_to(root).as_posix()
            for file in root.rglob("*")
            if file.is_file() and file.name != "complete.json"
        }
        if actual != set(records):
            return False
        if "scene.json" not in records or not any(
            name.startswith("undistorted/sparse/") for name in records
        ):
            return False
        for name, record in records.items():
            relative = Path(name)
            file = root / relative
            if relative.is_absolute() or ".." in relative.parts or file.is_symlink():
                return False
            if not file.resolve().is_relative_to(root.resolve()):
                return False
            if (
                file.stat().st_size != record["bytes"]
                or file_sha256(file) != record["sha256"]
            ):
                return False
        scene = json.loads((root / "scene.json").read_text())
        if not scene["views"] or not scene["points"]:
            return False
        return all(
            v["image"] in records and v["image"].startswith("undistorted/images/")
            for v in scene["views"]
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def restore_snapshot(root: Path, key: str, destination: Path) -> bool:
    if not verified_snapshot(root, key):
        return False
    scene = json.loads((root / "scene.json").read_text())
    destination.mkdir(parents=True)
    shutil.copytree(root / "undistorted", destination / "undistorted")
    for view in scene["views"]:
        view["image"] = str((destination / view["image"]).resolve())
    (destination / "scene.json").write_text(json.dumps(scene))
    return True


def publish_snapshot(source: Path, root: Path, key: str) -> None:
    if verified_snapshot(root, key):
        return
    root.parent.mkdir(parents=True, exist_ok=True)
    partial = Path(tempfile.mkdtemp(prefix=f".{key[:12]}-", dir=root.parent))
    try:
        scene = json.loads((source / "scene.json").read_text())
        for view in scene["views"]:
            relative = Path(view["image"]).resolve().relative_to(source.resolve())
            if not relative.as_posix().startswith("undistorted/images/"):
                raise ValueError("训练图像不属于当前 SfM 输出")
            view["image"] = relative.as_posix()
        for name in ("images", "sparse"):
            shutil.copytree(
                source / "undistorted" / name, partial / "undistorted" / name
            )
        (partial / "scene.json").write_text(json.dumps(scene))
        records = {
            file.relative_to(partial).as_posix(): {
                "bytes": file.stat().st_size,
                "sha256": file_sha256(file),
            }
            for file in sorted(partial.rglob("*"))
            if file.is_file()
        }
        (partial / "complete.json").write_text(
            json.dumps({"key": key, "files": records})
        )
        if not verified_snapshot(partial, key):
            raise ValueError("SfM 中间结果校验失败")
        # Invalid old caches are derived data. Never touch original capture assets.
        if root.exists():
            shutil.rmtree(root)
        os.replace(partial, root)
    finally:
        if partial.exists():
            shutil.rmtree(partial)
