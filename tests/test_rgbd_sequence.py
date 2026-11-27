import io
import tarfile

import numpy as np
from PIL import Image

from scripts.benchmark_rgbd_sequence import read_sequence


def test_timestamp_selection_matches_photo_import_without_ground_truth(tmp_path):
    prefix = "rgbd_dataset_freiburg1_xyz/"
    archive = tmp_path / "original.tgz"
    rgb = Image.fromarray(np.random.default_rng(7).integers(0, 256, (480, 640, 3), dtype=np.uint8))
    depth = Image.fromarray(np.full((480, 640), 1234, dtype=np.uint16))
    def png(image):
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue()
    with tarfile.open(archive, "w:gz") as bundle:
        def add(name, data):
            member = tarfile.TarInfo(prefix + name)
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
        def listed(folder, times):
            return "".join(f"{t} {folder}/{t}.png\n" for t in times).encode()
        add("rgb.txt", listed("rgb", range(1, 11)))
        add("depth.txt", listed("depth", [1, 10]))
        pixels = png(rgb)
        for t in range(1, 11):
            add(f"rgb/{t}.png", pixels)
        for t in (1, 10):
            add(f"depth/{t}.png", png(depth))
    frames, depths = read_sequence(archive, prefix, tmp_path / "input", 8)
    assert len(frames) == 8 and {frames[i][0] for i in depths} == {1, 10}
    assert np.array_equal(depths[0], np.asarray(depth))
    prepared = io.BytesIO()
    rgb.save(prepared, format="JPEG", quality=95)
    prepared.seek(0)
    with Image.open(prepared) as previous:
        normalized = io.BytesIO()
        previous.convert("RGB").save(normalized, format="JPEG", quality=94)
    assert frames[0][1].read_bytes() == normalized.getvalue()
