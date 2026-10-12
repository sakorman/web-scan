import hashlib
import io
import json
import tarfile

from fastapi.testclient import TestClient
from PIL import Image

from scripts.prepare_indoor import prepare
from webscan.api import create_app
from webscan.samples import SAMPLES, prepared_photos


def archive(tmp_path, count=14):
    path = tmp_path / "pipes.tar"
    with tarfile.open(path, "w") as bundle:
        for index in range(count):
            stream = io.BytesIO()
            Image.new("RGB", (1800, 40), (index * 17, 45, 120)).save(stream, "JPEG")
            raw = stream.getvalue()
            entry = tarfile.TarInfo(f"pipes/images/dslr_images/{index:03d}.JPG")
            entry.size = len(raw)
            bundle.addfile(entry, io.BytesIO(raw))
    return path


def test_prepare_and_import_real_photo_path(tmp_path):
    root = tmp_path / "samples"
    folder = prepare(archive(tmp_path), "pipes", root)
    manifest = json.loads((folder / "source.json").read_text())
    assert manifest["images"] == 14
    assert manifest["license"] == "CC BY-NC-SA 4.0"
    for entry in manifest["files"]:
        path = folder / entry["name"]
        with Image.open(path) as image:
            assert max(image.size) == 1600
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]

    app = create_app(tmp_path / "data", run_worker=False, sample_root=root)
    with TestClient(app) as client:
        samples = client.get("/api/samples").json()
        assert [item["available"] for item in samples] == [False, True]
        assert len(samples[1]["previews"]) == 3
        assert (
            client.get(samples[1]["previews"][0]).content
            == (folder / "000.jpg").read_bytes()
        )
        response = client.post("/api/samples/eth3d-pipes/import")
        assert response.status_code == 201, response.text
        session = response.json()
        assert session["kind"] == "photos" and session["status"] == "ready"
        assert len(session["frames"]) == 14
        assert [frame["metadata"]["original_name"] for frame in session["frames"]] == [
            f"{i:03d}.jpg" for i in range(14)
        ]
        provenance = json.loads(
            (app.state.store.session_dir(session["id"]) / "source.json").read_text()
        )
        assert provenance["name"] == "ETH3D pipes"
        assert provenance["license_url"].endswith("by-nc-sa/4.0/")


def test_partial_and_changed_samples_cannot_import(tmp_path):
    root = tmp_path / "samples"
    folder = prepare(archive(tmp_path), "pipes", root)
    original = (folder / "000.jpg").read_bytes()
    app = create_app(tmp_path / "data", run_worker=False, sample_root=root)
    with TestClient(app) as client:
        (folder / "000.jpg").unlink()
        assert client.get("/api/samples").json()[1]["available"] is False
        assert client.post("/api/samples/eth3d-pipes/import").status_code == 409
        (folder / "000.jpg").write_bytes(original + b"changed")
        assert client.post("/api/samples/eth3d-pipes/import").status_code == 409
        assert client.get("/api/sessions").json() == []
        assert client.get("/api/samples/eth3d-pipes/photos/-1").status_code == 404
        assert client.get("/api/samples/eth3d-pipes/photos/14").status_code == 404
        assert client.post("/api/samples/unknown/import").status_code == 404


def test_empty_folder_is_not_ready_and_legacy_import_still_checks(tmp_path):
    root = tmp_path / "samples"
    (root / "eth3d-office").mkdir(parents=True)
    with TestClient(
        create_app(tmp_path / "data", run_worker=False, sample_root=root)
    ) as client:
        assert client.get("/api/health").json()["indoor_sample_available"] is False
        assert client.post("/api/indoor-sample").status_code == 409
        assert client.get("/api/samples/eth3d-office/photos/0").status_code == 404


def test_manifest_cannot_point_outside_sample(tmp_path):
    root = tmp_path / "samples"
    folder = prepare(archive(tmp_path), "pipes", root)
    source = folder / "source.json"
    manifest = json.loads(source.read_text())
    manifest["files"][0]["name"] = "../outside.jpg"
    source.write_text(json.dumps(manifest))
    assert prepared_photos(root, SAMPLES["eth3d-pipes"]) == []


def test_incomplete_archive_preserves_prepared_sample(tmp_path):
    import pytest

    root = tmp_path / "samples"
    folder = prepare(archive(tmp_path), "pipes", root)
    previous = (folder / "source.json").read_bytes()
    with pytest.raises(ValueError, match="14"):
        prepare(archive(tmp_path, count=3), "pipes", root)
    assert (folder / "source.json").read_bytes() == previous
    assert len(prepared_photos(root, SAMPLES["eth3d-pipes"])) == 14
