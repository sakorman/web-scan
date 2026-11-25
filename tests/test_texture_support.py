import hashlib

import numpy as np
from PIL import Image
import pytest

from scripts.benchmark_texture_support import texture_support


def fixture(tmp_path, *, flat=False):
    image = np.zeros((51, 67), dtype=np.uint8)
    if not flat:
        image[:, 34:] = np.tile([0, 80, 255], (51, 11))
    path = tmp_path / "focus.png"
    Image.fromarray(image).save(path)
    audit = {
        "focus_image": path.name, "training_images": ["train.png"], "holdout_color_samples": 0,
        "photo_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()},
        "focus_dimensions": [67, 51],
        "focus_points": [{"point_id": 1, "training_observations": 2, "xy": [8.0, 6.0]}],
    }
    return audit, path


def test_uneven_tiles_and_contrast_ties(tmp_path):
    audit, path = fixture(tmp_path)
    result = texture_support(audit, path)
    assert result["tile_tracks"][1][1] == result["track_count"] == 1
    assert result["groups"]["low"]["occupied"] == result["groups"]["low"]["tracks"] == 1
    assert result["groups"]["high"]["tracks"] == 0
    assert result["groups"]["low"]["tiles"] > 16


def test_flat_reference_cannot_separate_groups(tmp_path):
    audit, path = fixture(tmp_path, flat=True)
    audit["focus_points"] = []
    result = texture_support(audit, path)
    assert result["groups"] == {}
    assert result["energy_quartiles"] == [0, 0]
    assert result["track_count"] == 0


@pytest.mark.parametrize("problem", ["identity", "dimensions", "split", "duplicate", "sources", "outside"])
def test_wrong_reference_or_track_rejected(tmp_path, problem):
    audit, path = fixture(tmp_path)
    point = audit["focus_points"][0]
    if problem == "identity":
        audit["photo_sha256"][path.name] = "0" * 64
    elif problem == "dimensions":
        audit["focus_dimensions"] = [51, 67]
    elif problem == "split":
        audit["training_images"].append(path.name)
    elif problem == "duplicate":
        audit["focus_points"].append(point)
    elif problem == "sources":
        point["training_observations"] = 1
    else:
        point["xy"] = [float("nan"), 10]
    with pytest.raises(ValueError):
        texture_support(audit, path)


def test_exact_scoring_grid_including_rounded_short_edge(tmp_path):
    audit, path = fixture(tmp_path)
    # Fitting office into this box a second time would yield a 511 px width.
    Image.new("L", (1600, 1067)).save(path)
    audit["focus_dimensions"] = [1600, 1067]
    audit["photo_sha256"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    result = texture_support(audit, path, scoring_size=(512, 341))
    assert result["size"] == [512, 341]
    assert result["track_count"] == 1
    with pytest.raises(ValueError, match="reference frame"):
        texture_support(audit, path, scoring_size=(512, 320))
