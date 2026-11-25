import numpy as np
import pytest

from scripts.benchmark_support_error import group_errors
from scripts.compare_quality import error_tiles


def test_support_error_weights_pixels_and_preserves_missing_groups():
    width, height = 43, 35
    pixels = np.arange(width * height, dtype=float).reshape(height, width, 1) / (width * height)
    tiles = error_tiles(pixels, 8)
    counts = np.zeros((8, 8), dtype=int)
    counts[2:6, 3:7] = 1
    support = {"size": [width, height], "tile_tracks": counts, "tile_gradient_energy": counts,
               "energy_quartiles": [0, 0.25]}
    result = group_errors(support, tiles)
    y0, y1 = 2 * height // 8, 6 * height // 8
    x0, x1 = 3 * width // 8, 7 * width // 8
    expected = pixels[y0:y1, x0:x1]
    assert result["all"]["supported"] == {
        "tiles": 16, "pixels": expected.size, "mse": pytest.approx(expected.mean())}
    assert result["low"]["supported"] == {"tiles": 0, "pixels": 0, "mse": None}
    assert result["high"]["empty"]["mse"] is None
    for label in ("empty", "supported"):
        assert result["all"][label]["pixels"] > 0
    support["energy_quartiles"] = [0, 0]
    assert set(group_errors(support, tiles)) == {"all"}
    with pytest.raises(ValueError, match="bounds"):
        group_errors(support, list(reversed(tiles)))
