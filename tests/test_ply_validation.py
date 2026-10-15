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
