import io
import json
import math
import subprocess
import sys

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageOps

from webscan import api
from webscan.api import create_app
from webscan.image_metadata import focal_length_35mm, focal_length_exif


def jpeg(focal=35, orientation=1):
    rng = np.random.default_rng(7)
    image = Image.fromarray(rng.integers(0, 256, (96, 128, 3), dtype="uint8"))
    exif = Image.Exif()
    exif[34665] = {41989: focal}
    exif[274] = orientation
    exif[271] = "Test maker"
    exif[272] = "Test model"
    exif[315] = "Private author"
    exif[34853] = {1: "N", 2: (30.0, 0.0, 0.0)}
    result = io.BytesIO()
    image.save(result, "JPEG", exif=exif)
    return result.getvalue()


def jpeg_size(size, focal=35):
    image = Image.new("RGB", size, "#245da8")
    exif = Image.Exif()
    exif[34665] = {41989: focal}
    result = io.BytesIO()
    image.save(result, "JPEG", quality=90, exif=exif)
    return result.getvalue()


def test_upload_preserves_focal_prior_without_other_exif_or_pixel_changes(tmp_path):
    raw = jpeg(35, orientation=6)
    with Image.open(io.BytesIO(raw)) as source:
        pixels = ImageOps.exif_transpose(source).convert("RGB")
        expected = io.BytesIO()
        pixels.save(expected, "JPEG", quality=94)
    app = create_app(tmp_path, run_worker=False)
    with TestClient(app) as client:
        sid = client.post("/api/sessions", json={}).json()["id"]
        response = client.post(
            f"/api/sessions/{sid}/frames", files={"file": ("photo.jpg", raw)}
        )
        assert response.status_code == 200
        frame = response.json()
        assert (frame["width"], frame["height"]) == (96, 128)
        saved = client.get(f"/api/sessions/{sid}/frames/{frame['id']}").content
        with (
            Image.open(io.BytesIO(saved)) as output,
            Image.open(io.BytesIO(expected.getvalue())) as baseline,
        ):
            assert output.getexif().get_ifd(34665) == {41989: 35}
            assert set(output.getexif()) == {34665}
            assert np.array_equal(np.asarray(output), np.asarray(baseline))
            assert focal_length_35mm(output) == 35
        duplicate = client.post(
            f"/api/sessions/{sid}/frames", files={"file": ("photo.jpg", raw)}
        ).json()
        assert duplicate["id"] == frame["id"] and duplicate["duplicate"]


def test_upload_safely_normalizes_larger_source_and_records_transform(
    tmp_path, monkeypatch
):
    # Use a scaled boundary so this checks the high-resolution branch without
    # allocating a production-sized photo during every unit-test run.
    monkeypatch.setattr(api, "MAX_SOURCE_PIXELS", 10_000)
    monkeypatch.setattr(api, "NORMALIZED_IMAGE_MAX_DIMENSION", 100)
    monkeypatch.setattr(api.Image, "MAX_IMAGE_PIXELS", 10_000)
    raw = jpeg_size((120, 80), focal=28)
    app = create_app(tmp_path, run_worker=False)
    with TestClient(app) as client:
        sid = client.post("/api/sessions", json={}).json()["id"]
        response = client.post(
            f"/api/sessions/{sid}/frames",
            data={"metadata": json.dumps({"capture_index": 3})},
            files={"file": ("large.jpg", raw)},
        )
        assert response.status_code == 200
        frame = response.json()
        assert (frame["width"], frame["height"]) == (100, 67)
        saved = client.get(f"/api/sessions/{sid}/frames/{frame['id']}").content
        with Image.open(io.BytesIO(saved)) as output:
            assert output.size == (100, 67)
            assert focal_length_35mm(output) == 28
        recorded = client.get(f"/api/sessions/{sid}").json()["frames"][0]["metadata"]
        assert recorded["capture_index"] == 3
        assert recorded["normalization"] == {
            "source_width": 120,
            "source_height": 80,
            "source_pixels": 9600,
            "normalized_width": 100,
            "normalized_height": 67,
            "resized": True,
        }


def test_upload_rejects_source_over_safe_pixel_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "MAX_SOURCE_PIXELS", 10_000)
    monkeypatch.setattr(api.Image, "MAX_IMAGE_PIXELS", 10_000)
    app = create_app(tmp_path, run_worker=False)
    with TestClient(app) as client:
        sid = client.post("/api/sessions", json={}).json()["id"]
        response = client.post(
            f"/api/sessions/{sid}/frames",
            files={"file": ("oversize.jpg", jpeg_size((125, 81)))},
        )
    assert response.status_code == 422
    assert "原始图片超过 4800 万像素" in response.json()["detail"]


@pytest.mark.parametrize(
    "value", [None, 0, -1, 65536, 20.5, float("nan"), float("inf"), "20", True]
)
def test_invalid_focal_hint_is_omitted(value):
    class Exif(dict):
        def get_ifd(self, key):
            return {41989: value}

    class Source:
        def getexif(self):
            return Exif()

    assert focal_length_exif(Source()) == b""
    assert focal_length_35mm(Source()) is None


def test_missing_or_broken_optional_metadata_is_omitted():
    assert focal_length_exif(Image.new("RGB", (64, 64))) == b""

    class Source:
        def getexif(self):
            raise ValueError("bad EXIF")

    assert focal_length_exif(Source()) == b""


def test_colmap_recognizes_focal_hint_after_normalization(tmp_path):
    # COLMAP and Torch have incompatible OpenMP runtimes on some platforms.
    images = tmp_path / "images"
    images.mkdir()
    with Image.open(io.BytesIO(jpeg(20))) as source:
        clean = source.convert("RGB")
        clean.save(images / "without.jpg", "JPEG", quality=94)
        clean.save(
            images / "with.jpg", "JPEG", quality=94, exif=focal_length_exif(source)
        )
    script = """
import json,sys
from pathlib import Path
import pycolmap
root=Path(sys.argv[1])
pycolmap.extract_features(root/'database.db',root/'images',device=pycolmap.Device.cpu,extraction_options={'num_threads':1})
with pycolmap.Database.open(root/'database.db') as db:
 print(json.dumps([{ 'focal':float(c.params[0]),'prior':c.has_prior_focal_length} for c in db.read_all_cameras()]))
"""
    run = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    cameras = json.loads(run.stdout.strip().splitlines()[-1])
    assert len(cameras) == 2
    prior = next(c for c in cameras if c["prior"])
    fallback = next(c for c in cameras if not c["prior"])
    assert prior["focal"] == pytest.approx(20 / 43.27 * math.hypot(128, 96))
    assert fallback["focal"] == pytest.approx(1.2 * 128)
