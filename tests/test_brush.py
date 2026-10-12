import numpy as np
import pytest

from webscan.brush import convert_output, read_ply
from webscan.gaussian import export_gaussians


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


def test_ply_export_preserves_directional_color_and_anisotropic_rotation(tmp_path):
    # A non-identity rotation and all SH3 coefficients catch accidental truncation
    # or xyzw/wxyz shuffling that identity-only examples would miss.
    names = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2"]
    names += [f"f_rest_{i}" for i in range(45)]
    names += ["opacity", "scale_0", "scale_1", "scale_2"]
    names += [f"rot_{i}" for i in range(4)]
    data = np.zeros(1, dtype=[(name, "<f4") for name in names])
    q = np.array([0.5, 0.5, -0.5, 0.5], dtype="f4")
    for i, value in enumerate(q):
        data[f"rot_{i}"] = value
    for i in range(45):
        data[f"f_rest_{i}"] = (i - 20) / 100
    for i, value in enumerate([0.1, 0.3, 0.8]):
        data[f"scale_{i}"] = np.log(value)
    source = tmp_path / "source.ply"
    header = "ply\nformat binary_little_endian 1.0\nelement vertex 1\n"
    header += "".join(f"property float {name}\n" for name in names) + "end_header\n"
    source.write_bytes(header.encode() + data.tobytes())
    output = tmp_path / "out"
    output.mkdir()
    convert_output(source, output, {"center": [0, 0, 0], "scale": 2})
    _, result = read_ply(output / "gaussians.ply")
    for name in names:
        if name.startswith(("rot_", "f_")):
            assert np.array_equal(result[name], data[name])
    for i, scale in enumerate([0.05, 0.15, 0.4]):
        assert np.allclose(np.exp(result[f"scale_{i}"]), scale)
