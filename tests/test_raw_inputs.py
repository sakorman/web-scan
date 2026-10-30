import json

import pytest
from PIL import Image

from webscan.raw_inputs import resolve_raw_input, snapshot_inputs
from webscan.sfm_cache import file_sha256


def capture(tmp_path):
    photo = tmp_path / "sessions/capture/images/frame.jpg"
    photo.parent.mkdir(parents=True)
    Image.new("RGB", (32, 24), "blue").save(photo)
    work = tmp_path / "jobs/run"
    work.mkdir(parents=True)
    manifest = {"frames": [{
        "id": "frame", "sha": file_sha256(photo), "width": 32, "height": 24,
    }]}
    snapshot_inputs(work, "capture", manifest)
    return photo, work, manifest


def test_warm_job_resolves_original_without_copying_photos(tmp_path):
    photo, work, _ = capture(tmp_path)
    resolved, digest, identity = resolve_raw_input(tmp_path, work, "0000-frame.jpg")
    assert resolved == photo and digest == file_sha256(photo)
    assert identity == "frozen_job_manifest"
    assert not (work / "input").exists()


@pytest.mark.parametrize("cold", [False, True])
def test_changed_photo_is_rejected_against_frozen_job_identity(tmp_path, cold):
    photo, work, manifest = capture(tmp_path)
    if cold:
        local = work / "input/0000-frame.jpg"
        local.parent.mkdir()
        local.write_bytes(photo.read_bytes())
        photo = local
    Image.new("RGB", (32, 24), "red").save(photo)
    # Even changing the live capture manifest cannot change this job's identity.
    manifest["frames"][0]["sha"] = file_sha256(photo)
    (tmp_path / "sessions/capture/manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="冻结摘要"):
        resolve_raw_input(tmp_path, work, "0000-frame.jpg")


def test_frozen_dimensions_and_order_are_enforced(tmp_path):
    photo, work, manifest = capture(tmp_path)
    with pytest.raises(ValueError, match="不在任务冻结清单"):
        resolve_raw_input(tmp_path, work, "0001-frame.jpg")
    manifest["frames"][0]["width"] = 31
    snapshot_inputs(work, "capture", manifest)
    with pytest.raises(ValueError, match="冻结尺寸"):
        resolve_raw_input(tmp_path, work, "0000-frame.jpg")
    photo.unlink()
    with pytest.raises(ValueError, match="冻结摘要"):
        resolve_raw_input(tmp_path, work, "0000-frame.jpg")


def test_historical_cold_job_remains_explicitly_unverified(tmp_path):
    photo = tmp_path / "jobs/old/input/0000-frame.jpg"
    photo.parent.mkdir(parents=True)
    Image.new("RGB", (32, 24), "blue").save(photo)
    path, digest, identity = resolve_raw_input(tmp_path, photo.parent.parent, photo.name)
    assert path == photo and digest == file_sha256(photo)
    assert identity == "legacy_job_input"
    with pytest.raises(ValueError, match="缺少原始照片与冻结清单"):
        resolve_raw_input(tmp_path, tmp_path / "jobs/old-warm", photo.name)


def test_raw_input_cannot_escape_capture_directory(tmp_path):
    _, work, _ = capture(tmp_path)
    with pytest.raises(ValueError, match="名称无效"):
        resolve_raw_input(tmp_path, work, "../0000-frame.jpg")
    descriptor = json.loads((work / "source.json").read_text())
    descriptor["session_id"] = "../outside"
    (work / "source.json").write_text(json.dumps(descriptor))
    with pytest.raises(ValueError, match="采集 ID 无效"):
        resolve_raw_input(tmp_path, work, "0000-frame.jpg")
