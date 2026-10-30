"""Reject broken model outputs before publishing downloads or pruning raw files."""

import shutil

import numpy as np
import pytest

from webscan.brush import convert_output, read_ply


def write_ply(path, degree=3, count=2, broken=None):
    names = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2"]
    names += [f"f_rest_{i}" for i in range(3 * ((degree + 1) ** 2 - 1))]
    names += ["opacity", "scale_0", "scale_1", "scale_2"]
    names += [f"rot_{i}" for i in range(4)]
    if broken == "missing":
        names.remove("f_rest_12")
    if broken == "gap":
        names[names.index("f_rest_12")] = "f_rest_45"
    data = np.zeros(count, dtype=[(name, "<f4") for name in names])
    data["rot_0"] = 1
    if broken in ("nan", "inf"):
        data["f_rest_44"][-1] = float(broken)
    header = ["ply", "format binary_little_endian 1.0", f"element vertex {count}"]
    header += [f"property float {name}" for name in names]
    header += ["end_header"]
    path.write_bytes(("\n".join(header) + "\n").encode() + data.tobytes())
    return data


@pytest.mark.parametrize("degree", [0, 1, 2, 3])
def test_complete_sh_orders_remain_readable_without_changing_coefficients(
    tmp_path, degree
):
    source = tmp_path / "valid.ply"
    original = write_ply(source, degree=degree)
    _, actual = read_ply(source, expected_sh_degree=degree)
    assert actual.tobytes() == original.tobytes()


@pytest.mark.parametrize("damage", ["nan", "inf", "missing", "gap", "empty"])
def test_broken_coefficients_and_empty_models_publish_no_download(tmp_path, damage):
    source = tmp_path / "broken.ply"
    write_ply(source, count=0 if damage == "empty" else 2, broken=damage)
    original = source.read_bytes()
    output = tmp_path / "output"
    output.mkdir()
    with pytest.raises(ValueError):
        convert_output(source, output, {"center": [0, 0, 0], "scale": 1})
    assert source.read_bytes() == original
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("row", [0, 20000, 40000])
@pytest.mark.parametrize(
    "field,value",
    [("x", float("nan")), ("opacity", float("inf")), ("f_rest_44", -float("inf"))],
)
def test_large_ply_rejects_invalid_values_across_records(tmp_path, row, field, value):
    source = tmp_path / "large.ply"
    data = write_ply(source, count=40001)
    # Reorder properties so validation must follow names, not fixed columns.
    names = list(reversed(data.dtype.names))
    reordered = np.empty(len(data), dtype=[(name, "<f4") for name in names])
    for name in names:
        reordered[name] = data[name]
    reordered[field][row] = value
    header = ["ply", "format binary_little_endian 1.0", f"element vertex {len(data)}"]
    header += [f"property float {name}" for name in names] + ["end_header"]
    with source.open("wb") as stream:
        stream.write(("\n".join(header) + "\n").encode())
        reordered.tofile(stream)
    output = tmp_path / "output"
    output.mkdir()
    with pytest.raises(ValueError, match="参数缺失或非有限"):
        convert_output(source, output, {"center": [0, 0, 0], "scale": 1})
    assert list(output.iterdir()) == []


def test_unused_float_properties_keep_existing_policy(tmp_path):
    source = tmp_path / "normals.ply"
    original = write_ply(source, count=40001)
    names = ["nx", *reversed(original.dtype.names)]
    data = np.empty(len(original), dtype=[(name, "<f4") for name in names])
    data["nx"] = np.nan
    for name in original.dtype.names:
        data[name] = original[name]
    header = ["ply", "format binary_little_endian 1.0", f"element vertex {len(data)}"]
    header += [f"property float {name}" for name in names] + ["end_header"]
    with source.open("wb") as stream:
        stream.write(("\n".join(header) + "\n").encode())
        data.tofile(stream)
    _, result = read_ply(source, expected_sh_degree=3)
    assert result.tobytes() == data.tobytes()


@pytest.mark.parametrize("damage", ["nan", "missing", "empty", "sh0"])
def test_successful_process_with_bad_ply_keeps_raw_exports(
    tmp_path, monkeypatch, damage
):
    from webscan import brush

    source = tmp_path / "source.ply"
    write_ply(
        source,
        degree=0 if damage == "sh0" else 3,
        count=0 if damage == "empty" else 2,
        broken=damage,
    )
    original = source.read_bytes()

    class Finished:
        returncode = 0

        def poll(self):
            return 0

    def done(args, stdout, **kwargs):
        for name in ["export_0500.ply", "export_1000.ply"]:
            shutil.copy2(source, tmp_path / "brush" / name)
        stdout.write(
            b"[time INFO brush_cli] Loaded dataset with 2 training, 0 eval views\n"
        )
        stdout.flush()
        return Finished()

    monkeypatch.setattr(brush, "executable", lambda: tmp_path / "brush-app")
    monkeypatch.setattr(brush, "engine_identity", lambda _: {})
    monkeypatch.setattr(brush.subprocess, "Popen", done)
    scene = {
        "views": [{"image": "a.jpg"}, {"image": "b.jpg"}],
        "normalization": {"center": [0, 0, 0], "scale": 1},
    }
    with pytest.raises(ValueError):
        brush.train(
            scene,
            tmp_path,
            tmp_path / "artifacts",
            {
                "steps": 1000,
                "resolution": 512,
                "max_points": 50000,
                "evaluation_mode": "all",
            },
            lambda *args: None,
        )
    assert list((tmp_path / "artifacts").iterdir()) == []
    for name in ["export_0500.ply", "export_1000.ply"]:
        assert (tmp_path / "brush" / name).read_bytes() == original
