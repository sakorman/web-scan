import io
import json
import math
import subprocess
import sys

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageOps

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
