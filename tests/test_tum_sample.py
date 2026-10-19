import hashlib
import io
import json
import tarfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from scripts.prepare_tum import prepare
from webscan.api import create_app
from webscan.samples import SAMPLES, prepared_photos


def archive(path, count=101, problem=None):
    with tarfile.open(path, "w:gz") as bundle:
        for index in reversed(range(count)):
            name = f"rgbd_dataset_freiburg1_desk/rgb/{1300000000 + index}.000.png"
            entry = tarfile.TarInfo(name)
            if index == 0 and problem == "link":
                entry.type = tarfile.SYMTYPE
                entry.linkname = "/outside.png"
                bundle.addfile(entry)
                continue
            if index == 0 and problem == "path":
                entry.name = "rgbd_dataset_freiburg1_desk/rgb/../1300000000.000.png"
            data = io.BytesIO()
            Image.new(
                "RGB",
                (64, 48) if index == 0 and problem == "size" else (640, 480),
                (index, 2 * index, 80),
            ).save(data, "PNG")
            raw = data.getvalue()
            entry.size = len(raw)
            bundle.addfile(entry, io.BytesIO(raw))
            if index == 0 and problem == "duplicate":
                bundle.addfile(entry, io.BytesIO(raw))
        # Neither calibration, ground truth nor depth participates in photo preparation.
        for name in ["depth/not-an-image.png", "groundtruth.txt", "accelerometer.txt"]:
            entry = tarfile.TarInfo("rgbd_dataset_freiburg1_desk/" + name)
            raw = b"not a photo or a calibration"
            entry.size = len(raw)
            bundle.addfile(entry, io.BytesIO(raw))
    return path


def test_tum_preparation_selects_ordered_rgb_only_and_imports_with_own_license(
    tmp_path,
):
    root = tmp_path / "samples"
    folder = prepare(archive(tmp_path / "source.tgz"), root)
    manifest = json.loads((folder / "source.json").read_text())
    assert manifest["original_rgb_frames"] == 101 and manifest["images"] == 96
    assert manifest["license"] == "CC BY 4.0"
    indices = [round(i * 100 / 95) for i in range(96)]
    names = [f"{1300000000 + i}.000.jpg" for i in indices]
    assert [item["name"] for item in manifest["files"]] == names
    assert len(list(folder.iterdir())) == 97
    for item in manifest["files"]:
        path = folder / item["name"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
        with Image.open(path) as image:
            assert image.size == (640, 480) and not image.getexif()
    app = create_app(tmp_path / "data", run_worker=False, sample_root=root)
    with TestClient(app) as client:
        catalog = client.get("/api/samples").json()
        assert catalog[-1]["available"] and catalog[-1]["license"] == "CC BY 4.0"
        response = client.post("/api/samples/tum-freiburg1_desk/import")
        assert response.status_code == 201, response.text
        session = response.json()
        assert session["name"] == "TUM RGB-D · 连续桌面"
        assert session["kind"] == "photos" and session["status"] == "ready"
        assert [
            frame["metadata"]["original_name"] for frame in session["frames"]
        ] == names
        assert all(
            frame["metadata"]["source"] == "TUM RGB-D freiburg1_desk"
            for frame in session["frames"]
        )
        source = json.loads(
            (app.state.store.session_dir(session["id"]) / "source.json").read_text()
        )
        assert (
            source["license"] == "CC BY 4.0" and "no depth" in source["preprocessing"]
        )


@pytest.mark.parametrize("problem", ["short", "link", "path", "duplicate", "size"])
def test_invalid_tum_archive_preserves_existing_sample(tmp_path, problem):
    root = tmp_path / "samples"
    source = tmp_path / "source.tgz"
    folder = prepare(archive(source), root)
    previous = {p.name: p.read_bytes() for p in folder.iterdir()}
    archive(source, count=90 if problem == "short" else 101, problem=problem)
    with pytest.raises(ValueError):
        prepare(source, root)
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == previous
    assert len(prepared_photos(root, SAMPLES["tum-freiburg1_desk"])) == 96
    assert sorted(p.name for p in root.iterdir()) == ["tum-freiburg1_desk"]
