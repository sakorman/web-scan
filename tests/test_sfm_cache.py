import json
from pathlib import Path

import pytest

from webscan.sfm_cache import (
    cache_key,
    file_sha256,
    publish_snapshot,
    restore_snapshot,
    validate_inputs,
    verified_snapshot,
)


def make_scene(root):
    image = root / "undistorted/images/0001.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"jpeg data")
    sparse = root / "undistorted/sparse"
    sparse.mkdir()
    (sparse / "cameras.bin").write_bytes(b"camera data")
    scene = {
        "views": [
            {
                "image": str(image.resolve()),
                "K": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                "view": [],
            }
        ],
        "points": [[1, 2, 3]],
        "colors": [[0.2, 0.3, 0.4]],
        "normalization": {"scale": 1},
    }
    (root / "scene.json").write_text(json.dumps(scene))
    return scene


def test_key_changes_for_order_image_or_engine_but_not_training_settings():
    frame = lambda i: {"id": str(i), "sha": str(i) * 64, "width": 100, "height": 100}
    manifest = {"frames": [frame(1), frame(2)]}
    key = cache_key(manifest, {"version": "a"})
    assert cache_key({**manifest, "steps": 6000}, {"version": "a"}) == key
    assert (
        cache_key({"frames": list(reversed(manifest["frames"]))}, {"version": "a"})
        != key
    )
    assert cache_key({"frames": [frame(1), frame(3)]}, {"version": "a"}) != key
    assert cache_key(manifest, {"version": "b"}) != key


def test_restored_scene_has_identical_geometry_and_new_local_image_paths(tmp_path):
    source = tmp_path / "run1"
    original = make_scene(source)
    cache = tmp_path / "cache/key"
    publish_snapshot(source, cache, "key")
    assert verified_snapshot(cache, "key")
    restored = tmp_path / "run2"
    assert restore_snapshot(cache, "key", restored)
    scene = json.loads((restored / "scene.json").read_text())
    assert (
        scene["points"] == original["points"]
        and scene["views"][0]["K"] == original["views"][0]["K"]
    )
    assert Path(scene["views"][0]["image"]).is_relative_to(restored)
    assert Path(scene["views"][0]["image"]).read_bytes() == b"jpeg data"
    (restored / "undistorted/images/0001.jpg").write_bytes(b"task-local change")
    assert verified_snapshot(cache, "key"), (
        "Restored files must not mutate the shared cache"
    )


def test_incomplete_or_corrupt_cache_is_never_reused(tmp_path):
    source = tmp_path / "run"
    make_scene(source)
    cache = tmp_path / "cache/key"
    cache.mkdir(parents=True)
    (cache / "scene.json").write_text("{}")
    assert not restore_snapshot(cache, "key", tmp_path / "incomplete")
    publish_snapshot(source, cache, "key")
    (cache / "undistorted/images/0001.jpg").write_bytes(b"corrupted")
    assert not restore_snapshot(cache, "key", tmp_path / "corrupt")
    assert not (tmp_path / "corrupt").exists()
    publish_snapshot(source, cache, "key")
    assert verified_snapshot(cache, "key")


def test_original_images_are_verified_even_if_cache_exists(tmp_path):
    image = tmp_path / "images/f.jpg"
    image.parent.mkdir()
    image.write_bytes(b"original")
    manifest = {"frames": [{"id": "f", "sha": file_sha256(image)}]}
    validate_inputs(tmp_path, manifest)
    image.write_bytes(b"changed")
    with pytest.raises(ValueError, match="校验失败"):
        validate_inputs(tmp_path, manifest)
