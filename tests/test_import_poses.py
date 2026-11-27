import json
import subprocess
import sys

from PIL import Image

from webscan.raw_inputs import resolve_raw_input, snapshot_inputs
from webscan.sfm_cache import restore_snapshot
from webscan.store import Store


def make_capture(root, names):
    sparse = root / "sparse/0"
    sparse.mkdir(parents=True)
    rows = []
    for i, name in enumerate(names, 1):
        image = root / "images" / name
        image.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 48), (40 * i, 30, 10)).save(image)
        rows.append(f"{i} 1 0 0 0 {-i} 0 0 1 {name}\n32 24 7\n")
    (sparse / "cameras.txt").write_text("1 SIMPLE_PINHOLE 64 48 50 32 24\n")
    (sparse / "images.txt").write_text("".join(rows))
    (sparse / "points3D.txt").write_text("7 0 0 3 10 20 30 0 1 0 2 0 3 0\n")
    return sparse


def invoke(source, data):
    # The COLMAP importer runs separately from a service that can load Torch.
    return subprocess.run([sys.executable, "-B", "-m", "webscan.import_poses",
                           str(source), "--data", str(data)], capture_output=True, text=True)


def test_posed_capture_preserves_native_files_and_raw_identity(tmp_path):
    source, data = tmp_path / "capture", tmp_path / "store"
    sparse = make_capture(source, ["a.jpg", "b.jpg", "c.jpg"])
    result = invoke(source, data)
    assert result.returncode == 0, result.stderr
    sid = result.stdout.strip()
    store = Store(data)
    assert store.session(sid)["kind"] == "posed"
    folder = store.session_dir(sid)
    manifest = json.loads((folder / "manifest.json").read_text())
    work = tmp_path / "work"
    work.mkdir()
    snapshot_inputs(work, sid, manifest)
    path, _, _ = resolve_raw_input(data, work, "b.jpg")
    assert path.read_bytes() == (source / "images/b.jpg").read_bytes()
    assert restore_snapshot(folder / "posed", manifest["posed_key"], work / "sfm")
    for original in sparse.iterdir():
        assert original.read_bytes() == (work / "sfm/undistorted/sparse" / original.name).read_bytes()


def test_posed_subdirectories_and_failed_import_are_isolated(tmp_path):
    source, data = tmp_path / "capture", tmp_path / "store"
    sparse = make_capture(source, ["a/rgb.png", "b/rgb.png", "c/rgb.png"])
    result = invoke(source, data)
    assert result.returncode == 0, result.stderr
    store = Store(data)
    sid = result.stdout.strip()
    manifest = json.loads((store.session_dir(sid) / "manifest.json").read_text())
    assert len({f["image_name"] for f in manifest["frames"]}) == 3
    assert all("/" not in f["image_name"] for f in manifest["frames"])
    (sparse / "cameras.txt").write_text("1 SIMPLE_RADIAL 64 48 50 32 24 0.03\n")
    failed = invoke(source, data)
    assert failed.returncode and "image_undistorter" in failed.stderr
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    assert [p.name for p in (data / "sessions").iterdir()] == [sid]
