import numpy as np
import pytest
from webscan.gaussian import export_gaussians
from webscan.brush import convert_output, read_ply


def test_brush_conversion_preserves_ply_and_normalizes_coordinates(tmp_path):
    source = tmp_path / "source"
    output = tmp_path / "output"
    output.mkdir()
    export_gaussians(
        source,
        np.array([[3.0, 4, 5]], dtype="f4"),
        np.ones((1, 3), dtype="f4") * 0.2,
        np.array([[1.0, 0, 0, 0]], dtype="f4"),
        np.array([0.5]),
        np.array([[0.1, 0.2, 0.3]]),
    )
    count = convert_output(
        source / "gaussians.ply", output, {"center": [1, 2, 3], "scale": 2}
    )
    _, points = read_ply(output / "gaussians.ply")
    assert count == 1 and points["x"][0] == 1 and points["y"][0] == 1
    assert np.allclose(np.exp(points["scale_0"]), 0.1)
    assert (output / "preview.splat").stat().st_size == 32


def test_rejects_truncated_ply(tmp_path):
    file = tmp_path / "bad.ply"
    file.write_bytes(
        b"ply\nformat binary_little_endian 1.0\nelement vertex 10\nproperty float x\nend_header\n"
    )
    with pytest.raises(ValueError):
        read_ply(file)
