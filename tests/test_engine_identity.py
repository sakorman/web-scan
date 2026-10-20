"""The selected executable, not the installer default, identifies a training run."""

import hashlib
import subprocess

import numpy as np
import pytest
from PIL import Image

from webscan import brush


def test_training_reports_selected_engine_and_explicit_seed(tmp_path, monkeypatch):
    binary = tmp_path / "custom-brush"
    binary.write_bytes(b"locally built engine")
    monkeypatch.setattr(brush, "executable", lambda: binary)
    monkeypatch.setattr(
        brush.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, b"brush-cli 0.3.0-local\n", b""
        ),
    )
    image = tmp_path / "photo.jpg"
    Image.new("RGB", (16, 16)).save(image)
    commands = []

    class Finished:
        returncode = 0

        def poll(self):
            return 0

    def done(args, stdout, **kwargs):
        commands.append(args)
        names = ["x", "y", "z"] + [f"f_dc_{i}" for i in range(3)]
        names += [f"f_rest_{i}" for i in range(45)]
        names += ["opacity"] + [f"scale_{i}" for i in range(3)]
        names += [f"rot_{i}" for i in range(4)]
        data = np.zeros(1, dtype=[(name, "<f4") for name in names])
        data["rot_0"] = 1
        header = "ply\nformat binary_little_endian 1.0\nelement vertex 1\n"
        header += "".join(f"property float {name}\n" for name in names)
        (tmp_path / "brush/export_500.ply").write_bytes(
            (header + "end_header\n").encode() + data.tobytes()
        )
        stdout.write(
            b"[test INFO brush_cli] Loaded dataset with 1 training, 0 eval views\n"
        )
        return Finished()

    monkeypatch.setattr(brush.subprocess, "Popen", done)
    report = brush.train(
        {
            "views": [{"image": str(image)}],
            "normalization": {"center": [0, 0, 0], "scale": 1},
        },
        tmp_path,
        tmp_path / "artifacts",
        {
            "steps": 500,
            "resolution": 512,
            "max_points": 50000,
            "evaluation_mode": "all",
        },
        lambda *args: None,
    )
    assert report["engine"] == "Brush 0.3.0-local"
    assert report["engine_version"] == "0.3.0-local"
    assert report["engine_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert report["training_seed"] == 42
    assert commands[0][commands[0].index("--seed") + 1] == "42"
    assert report["determinism_guaranteed"] is False
    assert report["evaluation"] is None
    assert (tmp_path / "artifacts/gaussians.ply").is_file()


@pytest.mark.parametrize(
    "result", [b"not a version", b"", b"brush-cli 0.3.0\nextra", "timeout", "failed"]
)
def test_unavailable_version_is_not_reported_as_installer_version(
    tmp_path, monkeypatch, result
):
    binary = tmp_path / "brush"
    binary.write_bytes(b"engine")

    def version(*args, **kwargs):
        if result == "timeout":
            raise subprocess.TimeoutExpired(args[0], 5)
        if result == "failed":
            raise subprocess.CalledProcessError(1, args[0])
        return subprocess.CompletedProcess(args, 0, result, b"")

    monkeypatch.setattr(brush.subprocess, "run", version)
    identity = brush.engine_identity(binary)
    assert identity["engine_version"] is None
    assert identity["engine"] == "Brush"
    assert identity["engine_sha256"] == hashlib.sha256(b"engine").hexdigest()


def test_same_path_and_version_with_different_bytes_changes_identity(
    tmp_path, monkeypatch
):
    binary = tmp_path / "brush"
    monkeypatch.setattr(
        brush.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, b"brush-cli 0.3.0\n", b""
        ),
    )
    binary.write_bytes(b"first build")
    first = brush.engine_identity(binary)
    binary.write_bytes(b"other build")
    second = brush.engine_identity(binary)
    assert first["engine_version"] == second["engine_version"] == "0.3.0"
    assert first["engine_sha256"] != second["engine_sha256"]
