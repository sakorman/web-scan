"""Exercise image registration and parallel feature extraction in a separate process."""

import json
import os
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image


@pytest.mark.parametrize("mode", ["auto", "shared"])
def test_parallel_features_keep_ordered_image_ids_and_camera_metadata(tmp_path, mode):
    images = tmp_path / "images"
    images.mkdir()
    # Creation order differs from capture-name order; feature work also differs.
    sizes = [(96, 64), (640, 480), (320, 240)]
    names = ["0002-last.jpg", "0000-first.jpg", "0001-middle.jpg"]
    for i, name in enumerate(names):
        width, height = sizes[i] if mode == "auto" else (320, 240)
        rng = np.random.default_rng(i)
        image = Image.fromarray(rng.integers(0, 255, (height, width, 3), dtype="uint8"))
        exif = Image.Exif()
        exif[34665] = {41989: 20 + (i * 8 if mode == "auto" else 0)}
        image.save(images / name, quality=95, exif=exif)
    script = """
import json,sys
from pathlib import Path
import pycolmap
from webscan.reconstruction import estimate_scene
root=Path(sys.argv[1]);mode=sys.argv[2]
class FeaturesReady(Exception): pass
def stop_matching(**kwargs): raise FeaturesReady()
pycolmap.match_exhaustive=stop_matching
records=[]
for attempt in range(2):
    work=root/str(attempt)
    try: estimate_scene(root/'images',work,lambda *args:None,camera_mode=mode)
    except FeaturesReady: pass
    else: raise AssertionError('Feature stage did not finish')
    with pycolmap.Database.open(work/'database.db') as db:
        images=sorted(db.read_all_images(),key=lambda x:x.image_id)
        cameras={c.camera_id:c for c in db.read_all_cameras()}
        records.append([{'id':i.image_id,'name':i.name,'camera':i.camera_id,
                         'width':cameras[i.camera_id].width,'height':cameras[i.camera_id].height,
                         'focal':float(cameras[i.camera_id].params[0]),
                         'prior':cameras[i.camera_id].has_prior_focal_length,
                         'features':db.read_keypoints(i.image_id).shape[0]}
                        for i in images])
print(json.dumps(records))
"""
    run = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), mode],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "OMP_NUM_THREADS": "4", "OPENBLAS_NUM_THREADS": "4"},
    )
    records = json.loads(run.stdout.strip().splitlines()[-1])
    assert records[0] == records[1]
    assert [r["name"] for r in records[0]] == sorted(names)
    assert [r["id"] for r in records[0]] == [1, 2, 3]
    assert all(r["prior"] and r["features"] > 0 for r in records[0])
    assert len({r["camera"] for r in records[0]}) == (3 if mode == "auto" else 1)
    for record in records[0]:
        i = names.index(record["name"])
        expected = sizes[i] if mode == "auto" else (320, 240)
        focal = 20 + (i * 8 if mode == "auto" else 0)
        assert (record["width"], record["height"]) == expected
        assert record["focal"] == pytest.approx(focal / 43.27 * np.hypot(*expected))
