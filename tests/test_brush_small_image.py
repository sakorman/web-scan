import numpy as np
from PIL import Image

from webscan.brush import training_inputs


def test_short_jpeg_keeps_pixels_metadata_and_frozen_input(tmp_path):
    source = tmp_path / "undistorted"
    images = source / "images"
    images.mkdir(parents=True)
    (source / "sparse").mkdir()
    path = images / "desk.jpg"
    metadata = Image.Exif()
    metadata[271] = "camera"
    Image.new("RGB", (128, 96), "teal").save(path, exif=metadata)
    original = path.read_bytes()
    with Image.open(path) as src:
        pixels = np.asarray(src).copy()
    with training_inputs(source, "0.3.0") as (staged, count):
        assert staged != source and count == 1
        copy = staged / "images" / path.name
        assert copy.stat().st_size >= 16387
        with Image.open(copy) as checked:
            np.testing.assert_array_equal(np.asarray(checked), pixels)
            assert checked.getexif()[271] == "camera"
    assert path.read_bytes() == original and not staged.exists()
    with training_inputs(source, "0.4.0") as (unchanged, count):
        assert unchanged == source and count == 0
