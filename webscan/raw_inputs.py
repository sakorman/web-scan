"""Frozen photo identities for offline evaluation of cold and cached jobs."""

import json
from pathlib import Path

from PIL import Image

from .sfm_cache import file_sha256


def snapshot_inputs(work: Path, session_id: str, manifest: dict) -> None:
    descriptor = {
        "version": 1,
        "session_id": session_id,
        "frames": [
            {key: frame[key] for key in ("id", "sha", "width", "height")}
            for frame in manifest["frames"]
        ],
    }
    (work / "source.json").write_text(json.dumps(descriptor))


def resolve_raw_input(data: Path, work: Path, image_name: str):
    """Prefer the job copy; a warm job can use the verified capture original.

    Historical cold jobs have only their local input copies. Never guess a
    camera or borrow one from a different reconstruction for an old warm job.
    """
    if Path(image_name).name != image_name:
        raise ValueError("原始照片名称无效")
    local = work / "input" / image_name
    descriptor = work / "source.json"
    if not descriptor.exists():
        if not local.is_file():
            raise ValueError(f"缺少原始照片与冻结清单：{image_name}")
        return local, file_sha256(local), "legacy_job_input"
    source = json.loads(descriptor.read_text())
    if source.get("version") != 1:
        raise ValueError("不支持的原始照片清单版本")
    session_id = source["session_id"]
    if not session_id or session_id in (".", "..") or Path(session_id).name != session_id:
        raise ValueError("原始照片清单的采集 ID 无效")
    matches = [
        frame for index, frame in enumerate(source["frames"])
        if f"{index:04d}-{frame['id']}.jpg" == image_name
    ]
    if len(matches) != 1:
        raise ValueError(f"原始照片不在任务冻结清单中：{image_name}")
    frame = matches[0]
    path = local if local.is_file() else data / "sessions" / session_id / "images" / f"{frame['id']}.jpg"
    if not path.is_file() or file_sha256(path) != frame["sha"]:
        raise ValueError(f"原始照片与任务冻结摘要不一致：{image_name}")
    with Image.open(path) as image:
        if image.size != (frame["width"], frame["height"]):
            raise ValueError(f"原始照片与任务冻结尺寸不一致：{image_name}")
    return path, frame["sha"], "frozen_job_manifest"
