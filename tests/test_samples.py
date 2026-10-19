import hashlib
import io
import json
import subprocess
import sys
import tarfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from scripts.prepare_indoor import prepare, read_archive
from webscan.api import create_app
from webscan.samples import SAMPLES, prepared_photos


def archive(tmp_path, count=14, focal=None):
    path = tmp_path / "pipes.tar"
    with tarfile.open(path, "w") as bundle:
        for index in range(count):
            stream = io.BytesIO()
            exif = Image.Exif()
            if focal is not None:
                exif[34665] = {41989: focal}
            Image.new("RGB", (1800, 40), (index * 17, 45, 120)).save(
                stream, "JPEG", exif=exif
            )
            raw = stream.getvalue()
            entry = tarfile.TarInfo(f"pipes/images/dslr_images/{index:03d}.JPG")
            entry.size = len(raw)
            bundle.addfile(entry, io.BytesIO(raw))
    return path


def test_prepare_and_import_real_photo_path(tmp_path):
    root = tmp_path / "samples"
    folder = prepare(archive(tmp_path, focal=20), "pipes", root)
    manifest = json.loads((folder / "source.json").read_text())
    assert manifest["images"] == 14
    assert manifest["license"] == "CC BY-NC-SA 4.0"
    for entry in manifest["files"]:
        path = folder / entry["name"]
        with Image.open(path) as image:
            assert max(image.size) == 1600
            assert image.getexif().get_ifd(34665)[41989] == 20
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]

    app = create_app(tmp_path / "data", run_worker=False, sample_root=root)
    with TestClient(app) as client:
        samples = client.get("/api/samples").json()
        assert [item["available"] for item in samples] == [False, True, False]
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
        first_frame = session["frames"][0]["id"]
        saved = client.get(
            f"/api/sessions/{session['id']}/frames/{first_frame}"
        ).content
        with Image.open(io.BytesIO(saved)) as image:
            assert image.getexif().get_ifd(34665)[41989] == 20
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
    root = tmp_path / "samples"
    folder = prepare(archive(tmp_path), "pipes", root)
    previous = (folder / "source.json").read_bytes()
    with pytest.raises(ValueError, match="14"):
        prepare(archive(tmp_path, count=3), "pipes", root)
    assert (folder / "source.json").read_bytes() == previous
    assert len(prepared_photos(root, SAMPLES["eth3d-pipes"])) == 14


@pytest.mark.parametrize(
    "problem", ["duplicate", "path", "symlink", "oversized", "broken"]
)
def test_bad_archive_preserves_every_prepared_file(tmp_path, problem):
    root = tmp_path / "samples"
    path = archive(tmp_path)
    folder = prepare(path, "pipes", root)
    previous = {item.name: item.read_bytes() for item in folder.iterdir()}
    path = archive(tmp_path, count=13)
    with tarfile.open(path, "a") as bundle:
        entry = tarfile.TarInfo("pipes/images/dslr_images/extra.JPG")
        raw = b"not an image"
        if problem == "duplicate":
            # Distinct extension spelling must not overwrite the same output JPEG.
            entry.name = "pipes/images/dslr_images/000.jpg"
        elif problem == "path":
            entry.name = "pipes/images/dslr_images/../outside.JPG"
        elif problem == "symlink":
            entry.type = tarfile.SYMTYPE
            entry.linkname = "000.JPG"
        elif problem == "oversized":
            entry.size = 65 * 1024 * 1024
            bundle.addfile(entry)
        if problem != "oversized":
            entry.size = len(raw) if entry.isfile() else 0
            bundle.addfile(entry, io.BytesIO(raw) if entry.isfile() else None)
    with pytest.raises((ValueError, OSError, tarfile.TarError)):
        prepare(path, "pipes", root)
    assert {item.name: item.read_bytes() for item in folder.iterdir()} == previous
    assert sorted(item.name for item in root.iterdir()) == ["eth3d-pipes"]
    assert not (tmp_path / "outside.JPG").exists()


def test_stream_order_does_not_change_manifest(tmp_path):
    original = archive(tmp_path, focal=20)
    reversed_path = tmp_path / "reversed.tar.gz"
    with (
        tarfile.open(original) as source,
        tarfile.open(reversed_path, "w:gz") as target,
    ):
        for entry in reversed(source.getmembers()):
            target.addfile(entry, source.extractfile(entry))
    first = prepare(original, "pipes", tmp_path / "first")
    second = prepare(reversed_path, "pipes", tmp_path / "second")
    assert {p.name: p.read_bytes() for p in first.iterdir()} == {
        p.name: p.read_bytes() for p in second.iterdir()
    }


@pytest.mark.parametrize("failure", [False, True])
def test_7z_producer_must_finish_before_publish(tmp_path, monkeypatch, failure):
    root = tmp_path / "samples"
    fixture = archive(tmp_path)
    folder = prepare(fixture, "pipes", root)
    previous = {item.name: item.read_bytes() for item in folder.iterdir()}
    # Stand in for libarchive so this contract also runs on GNU tar CI hosts.
    encoded = tmp_path / "input.7z"
    encoded.write_bytes(b"7z\xbc\xaf\x27\x1c")
    real_popen = subprocess.Popen
    processes = []

    def producer(args, **kwargs):
        assert args == ["tar", "-cf", "-", f"@{encoded.resolve()}"]
        process = real_popen(
            [
                sys.executable,
                "-c",
                (
                    "import pathlib,sys; "
                    "sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes()); "
                    "sys.stderr.write('decode failure'); "
                    f"sys.exit({2 if failure else 0})"
                ),
                str(fixture),
            ],
            **kwargs,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", producer)
    if failure:
        with pytest.raises(ValueError, match="decode failure"):
            prepare(encoded, "pipes", root)
    else:
        prepare(encoded, "pipes", root)
    assert len(processes) == 1 and processes[0].poll() is not None
    assert {item.name: item.read_bytes() for item in folder.iterdir()} == previous
    assert sorted(item.name for item in root.iterdir()) == ["eth3d-pipes"]


def test_truncated_archive_preserves_prepared_sample(tmp_path):
    root = tmp_path / "samples"
    path = archive(tmp_path)
    folder = prepare(path, "pipes", root)
    previous = {item.name: item.read_bytes() for item in folder.iterdir()}
    path.write_bytes(path.read_bytes()[:25000])
    with pytest.raises(tarfile.ReadError):
        prepare(path, "pipes", root)
    assert {item.name: item.read_bytes() for item in folder.iterdir()} == previous


def test_consumer_error_stops_archive_producer(tmp_path, monkeypatch):
    fixture = archive(tmp_path)
    encoded = tmp_path / "input.7z"
    encoded.write_bytes(b"7z\xbc\xaf\x27\x1c")
    real_popen = subprocess.Popen
    processes = []

    def producer(_args, **kwargs):
        process = real_popen(
            [
                sys.executable,
                "-c",
                (
                    "import pathlib,sys,time; "
                    "sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes()); "
                    "sys.stdout.flush(); time.sleep(30)"
                ),
                str(fixture),
            ],
            **kwargs,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", producer)
    with (
        pytest.raises(RuntimeError, match="stop reading"),
        read_archive(encoded) as bundle,
    ):
        assert next(iter(bundle)).isfile()
        raise RuntimeError("stop reading")
    assert len(processes) == 1 and processes[0].poll() is not None
