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


def test_training_progress_reads_complete_lines_without_export_files(tmp_path):
    from webscan.brush import TrainingProgress

    path = tmp_path / "brush.log"
    tracker = TrainingProgress(6000)
    tracker.read(path)
    assert tracker.step == 0
    path.write_bytes(b"[time INFO brush_cli] Refine iter 101, 2200 spl")
    tracker.read(path)
    assert tracker.step == 0
    with path.open("ab") as stream:
        stream.write(b"ats.\n[time INFO brush_cli] Refine iter 201, 2500 splats.\n")
    tracker.read(path)
    assert (tracker.step, tracker.splats) == (201, 2500)
    tracker.read(path)
    assert tracker.step == 201
    assert not list(tmp_path.glob("*.ply"))


def test_training_progress_ignores_untrusted_numbers_and_never_regresses(tmp_path):
    from webscan.brush import TrainingProgress

    path = tmp_path / "brush.log"
    path.write_bytes(b"[time INFO brush_cli] Refine iter 501, 3000 splats.\n")
    tracker = TrainingProgress(6000)
    tracker.read(path)
    with path.open("ab") as stream:
        stream.write(b"[time INFO other] Refine iter 999, 999 splats.\n")
        stream.write(b"[time INFO brush_cli] Refine iter 999999, 9000 splats.\n")
        stream.write(b"[time INFO brush_cli] Refine iter 901, 99999999 splats.\n")
        stream.write(b"[time INFO brush_cli] Refine iter 401, 2000 splats.\n")
        stream.write(b"[time INFO brush_cli] Refine iter 601, 3200 splats.\n")
    tracker.read(path)
    assert (tracker.step, tracker.splats) == (601, 3200)


def test_training_progress_bounds_large_log_reads_and_handles_split_messages(tmp_path):
    from webscan.brush import TrainingProgress, log_tail

    path = tmp_path / "brush.log"
    # A refinement record straddles the 64 KiB read boundary.
    path.write_bytes(
        b"x" * (64 * 1024 - 12)
        + b"\n[time INFO brush_cli] Refine iter 101, 1234 splats.\n"
    )
    tracker = TrainingProgress(6000)
    tracker.read(path)
    assert tracker.offset == 64 * 1024
    assert tracker.step == 0 and len(tracker.pending) <= 512
    tracker.read(path)
    assert (tracker.step, tracker.splats) == (101, 1234)
    assert log_tail(path, 8) == "splats.\n"


@pytest.mark.parametrize("name", ["export_5999.ply", "export_9999.ply", None])
def test_successful_process_must_have_exact_final_model(tmp_path, monkeypatch, name):
    from webscan import brush

    class Finished:
        returncode = 0

        def poll(self):
            return 0

    def finished_process(args, **kwargs):
        folder = tmp_path / "brush"
        if name:
            (folder / name).write_bytes(b"not a final model")
        return Finished()

    monkeypatch.setattr(brush, "executable", lambda: tmp_path / "fake-brush")
    monkeypatch.setattr(brush, "engine_identity", lambda _: {})
    monkeypatch.setattr(brush.subprocess, "Popen", finished_process)
    with pytest.raises(RuntimeError, match="未输出最终模型"):
        brush.train(
            {},
            tmp_path,
            tmp_path / "artifacts",
            {"steps": 6000, "max_points": 150000, "resolution": 1024},
            lambda *args: None,
        )


def test_cleanup_only_removes_this_runs_earlier_exports_after_success(tmp_path):
    from webscan.brush import prune_intermediate_exports

    (tmp_path / "export_0500.ply").write_bytes(b"old")
    (tmp_path / "export_6000.ply").write_bytes(b"final")
    (tmp_path / "export_7000.ply").write_bytes(b"later")
    (tmp_path / "notes.txt").write_bytes(b"notes")
    (tmp_path / "export_1000.ply").mkdir()
    (tmp_path / "export_1500.ply").symlink_to(tmp_path / "export_6000.ply")
    removed, errors = prune_intermediate_exports(tmp_path, 6000)
    assert removed == 3 and errors == []
    assert not (tmp_path / "export_0500.ply").exists()
    assert (tmp_path / "export_6000.ply").read_bytes() == b"final"
    assert (tmp_path / "export_7000.ply").read_bytes() == b"later"
    assert (tmp_path / "export_1500.ply").is_symlink()
    assert (tmp_path / "export_1000.ply").is_dir()
    assert (tmp_path / "notes.txt").read_bytes() == b"notes"


def test_cleanup_failure_preserves_final_model_and_reports_leftovers(
    tmp_path, monkeypatch
):
    from pathlib import Path

    from webscan.brush import prune_intermediate_exports

    (tmp_path / "export_0500.ply").write_bytes(b"old")
    (tmp_path / "export_6000.ply").write_bytes(b"final")

    def deny_unlink(self, *args, **kwargs):
        raise PermissionError("read-only test")

    monkeypatch.setattr(Path, "unlink", deny_unlink)
    removed, errors = prune_intermediate_exports(tmp_path, 6000)
    assert removed == 0 and errors == ["export_0500.ply"]
    assert (tmp_path / "export_6000.ply").read_bytes() == b"final"
