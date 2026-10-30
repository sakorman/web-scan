"""Adapter for the unmodified Apache-2.0 Brush 0.3.0 command-line engine."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image

from webscan.evaluation import validate_evaluation

TRAINING_SEED = 42


def engine_identity(binary):
    """Describe the selected executable without assuming it is the pinned release."""
    with binary.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    version = None
    try:
        result = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            check=True,
            timeout=5,
        )
        match = re.fullmatch(
            r"brush(?:-cli|_app)? ([0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.+-]+)?)",
            result.stdout.decode("utf-8", errors="replace").strip(),
        )
        if match:
            version = match[1]
    except (OSError, subprocess.SubprocessError):
        # A wrapper may train correctly without supporting a version query.
        # Keep its file identity, but never substitute the installer's version.
        pass
    return {
        "engine": f"Brush {version}" if version else "Brush",
        "engine_version": version,
        "engine_sha256": digest,
    }


def executable():
    configured = os.environ.get("WEBSCAN_BRUSH")
    path = (
        Path(configured)
        if configured
        else Path(__file__).resolve().parent.parent / ".tools/brush/brush_app"
    )
    return path.resolve() if path.is_file() and os.access(path, os.X_OK) else None


def read_ply(path, *, expected_sh_degree=None):
    with path.open("rb") as file:
        header = []
        fields = []
        count = None
        for _ in range(256):
            line = file.readline().decode("ascii").strip()
            header.append(line)
            if line.startswith("element vertex "):
                count = int(line.split()[-1])
            elif line.startswith("property "):
                parts = line.split()
                if len(parts) != 3 or parts[1] not in ("float", "float32"):
                    raise ValueError("不支持的 Gaussian PLY 属性类型")
                fields.append((parts[2], "<f4"))
            elif line == "end_header":
                break
        else:
            raise ValueError("PLY header 无效")
        if (
            "format binary_little_endian 1.0" not in header
            or count is None
            or not 0 < count <= 2_000_000
        ):
            raise ValueError("不支持的 PLY 格式或点数")
        sh_fields = {name for name, _ in fields if name.startswith("f_rest_")}
        if len(sh_fields) not in (0, 9, 24, 45) or sh_fields != {
            f"f_rest_{i}" for i in range(len(sh_fields))
        }:
            raise ValueError("PLY 球谐系数不完整")
        if expected_sh_degree is not None and len(sh_fields) != 3 * (
            (expected_sh_degree + 1) ** 2 - 1
        ):
            raise ValueError(f"训练输出必须包含完整 SH{expected_sh_degree} 系数")
        data = np.fromfile(file, dtype=fields, count=count)
        if len(data) != count:
            raise ValueError("PLY 内容不完整")
        required = [
            "x",
            "y",
            "z",
            "scale_0",
            "scale_1",
            "scale_2",
            "rot_0",
            "rot_1",
            "rot_2",
            "rot_3",
            "opacity",
            "f_dc_0",
            "f_dc_1",
            "f_dc_2",
            *sorted(sh_fields),
        ]
        if any(key not in data.dtype.names for key in required):
            raise ValueError("PLY 参数缺失或非有限")
        # All parsed fields are packed little-endian float32. Scan consecutive
        # records once instead of revisiting the interleaved buffer per field.
        # Limit the temporary boolean array to 1 MiB and retain the existing
        # policy for extra properties (e.g. unused normals).
        columns = [data.dtype.names.index(key) for key in required]
        values = data.view("<f4").reshape(count, len(fields))
        block_rows = max(1, (1024 * 1024) // len(fields))
        for start in range(0, count, block_rows):
            finite = np.isfinite(values[start : start + block_rows]).all(axis=0)
            if not finite[columns].all():
                raise ValueError("PLY 参数缺失或非有限")
    return header, data


def convert_output(source, output, normalization, *, expected_sh_degree=None):
    header, data = read_ply(source, expected_sh_degree=expected_sh_degree)
    center = np.array(normalization["center"])
    radius = float(normalization["scale"])
    for i, axis in enumerate("xyz"):
        data[axis] = (data[axis] - center[i]) / radius
        data[f"scale_{i}"] -= np.log(radius)
    with (output / "gaussians.ply").open("wb") as f:
        f.write(("\n".join(header) + "\n").encode())
        # Keep the explicit little-endian records without a full bytes copy.
        data.tofile(f)
    splat = np.zeros(
        len(data),
        dtype=[
            ("xyz", "<f4", 3),
            ("scale", "<f4", 3),
            ("rgba", "u1", 4),
            ("quat", "u1", 4),
        ],
    )
    splat["xyz"] = np.column_stack([data[x] for x in "xyz"])
    splat["scale"] = np.exp(
        np.clip(np.column_stack([data[f"scale_{i}"] for i in range(3)]), -20, 10)
    )
    rgb = (
        np.column_stack([data[f"f_dc_{i}"] for i in range(3)]) * 0.28209479177387814
        + 0.5
    )
    opacity = 1 / (1 + np.exp(-np.clip(data["opacity"], -30, 30)))
    splat["rgba"] = np.clip(np.column_stack([rgb, opacity]) * 255, 0, 255).astype("u1")
    quat = np.column_stack([data[f"rot_{i}"] for i in range(4)])
    quat /= np.maximum(np.linalg.norm(quat, axis=1, keepdims=True), 1e-8)
    splat["quat"] = np.clip(quat * 128 + 128, 0, 255).astype("u1")
    splat.tofile(output / "preview.splat")
    return len(data)


class TrainingProgress:
    """Read bounded chunks of complete Brush 0.3.0 refinement log lines."""

    pattern = re.compile(rb"\bbrush_cli\] Refine iter (\d+), (\d+) splats\.")

    def __init__(self, total_steps):
        self.total_steps = total_steps
        self.step = 0
        self.splats = None
        self.offset = 0
        self.pending = b""

    def read(self, path):
        try:
            with path.open("rb") as stream:
                stream.seek(self.offset)
                chunk = stream.read(64 * 1024)
                self.offset = stream.tell()
        except FileNotFoundError:
            return
        lines = (self.pending + chunk).split(b"\n")
        # Dataset debug lines can be very long; only the tail of an unfinished
        # line is needed to identify the small refinement message.
        self.pending = lines.pop()[-512:]
        for line in lines:
            match = self.pattern.search(line)
            if match:
                step, splats = map(int, match.groups())
                if self.step < step <= self.total_steps and 0 < splats <= 2_000_000:
                    self.step, self.splats = step, splats


def log_tail(path, limit=2000):
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - limit))
        return stream.read(limit).decode(errors="replace")


def prune_intermediate_exports(exports, steps):
    """Clean only files produced by this fresh training run; preserve the final PLY."""
    removed_bytes = 0
    errors = []
    for file in exports.iterdir():
        match = re.fullmatch(r"export_(\d+)\.ply", file.name)
        if not match or not 0 < int(match[1]) < steps:
            continue
        if file.is_symlink() or not file.is_file():
            continue
        try:
            size = file.stat().st_size
            file.unlink()
            removed_bytes += size
        except OSError:
            # Housekeeping failure must not discard a successfully trained model.
            errors.append(file.name)
    return removed_bytes, errors


def train(scene, work, output, config, progress):
    binary = executable()
    if not binary:
        raise RuntimeError("未安装 Brush；请运行 scripts/install_brush.py。")
    identity_started = time.monotonic()
    identity = engine_identity(binary)
    identity_seconds = time.monotonic() - identity_started
    output.mkdir(parents=True, exist_ok=True)
    exports = work / "brush"
    exports.mkdir()
    steps = config["steps"]
    # Keep the engine's established export cadence. Only remove intermediate
    # files after the final model has been validated and converted successfully.
    interval = min(500, steps)
    holdout = config.get("evaluation_mode", "holdout") == "holdout"
    args = [
        str(binary),
        str(work / "sfm/undistorted"),
        "--seed",
        str(TRAINING_SEED),
        "--total-steps",
        str(steps),
        "--max-splats",
        str(config["max_points"]),
        "--max-resolution",
        str(config["resolution"]),
        "--refine-every",
        "100",
        "--growth-stop-iter",
        str(int(steps * 0.8)),
        "--export-every",
        str(interval),
        "--export-path",
        str(exports),
        "--export-name",
        "export_{iter}.ply",
    ]
    if holdout:
        args += [
            "--eval-split-every",
            "8",
            "--eval-every",
            str(steps),
            "--eval-save-to-disk",
        ]
    log_path = work / "brush.log"
    tracker = TrainingProgress(steps)
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            args,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "RUST_LOG": "info"},
        )
        start = time.monotonic()
        try:
            while process.poll() is None:
                if time.monotonic() - start > 3600:
                    raise RuntimeError("GPU 训练超过一小时限制")
                tracker.read(log_path)
                message = (
                    f"GPU 训练 {tracker.step}/{steps} 步 · {tracker.splats:,} 个高斯"
                    if tracker.step
                    else "GPU 正在准备训练"
                )
                progress(
                    "train",
                    0.42 + 0.5 * tracker.step / steps,
                    f"{message} · {int(time.monotonic() - start)} 秒",
                )
                # Keep the progress cadence, but begin validation as soon as
                # the child exits instead of sleeping through its completion.
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
            if process.returncode:
                detail = log_tail(log_path)
                raise RuntimeError("Brush GPU 训练失败：" + detail)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
    process_seconds = time.monotonic() - start
    final_model = exports / f"export_{steps:0{len(str(steps))}d}.ply"
    if not final_model.is_file():
        raise RuntimeError("训练器未输出最终模型")
    progress("train", 0.92, "训练完成，正在校验并转换模型")
    text = log_path.read_text(errors="replace")
    evaluation, rendered = validate_evaluation(scene, exports, config, text)
    count = convert_output(
        final_model, output, scene["normalization"], expected_sh_degree=3
    )
    thumbnail = rendered[0] if rendered else Path(scene["views"][0]["image"])
    with Image.open(thumbnail) as image:
        image.convert("RGB").resize(
            (480, round(image.height * 480 / image.width))
        ).save(output / "thumbnail.jpg")
    written = list(exports.glob("export_*.ply"))
    written_bytes = sum(file.stat().st_size for file in written)
    removed_bytes, cleanup_errors = prune_intermediate_exports(exports, steps)
    return {
        "backend": "brush",
        **identity,
        "engine_identity_seconds": round(identity_seconds, 4),
        "training_seed": TRAINING_SEED,
        "determinism_guaranteed": False,
        "export_policy": "periodic_final_retained",
        "export_written_files": len(written),
        "export_written_bytes": written_bytes,
        "export_removed_bytes": removed_bytes,
        "export_cleanup_errors": cleanup_errors,
        "export_files": len(list(exports.glob("export_*.ply"))),
        "export_bytes": sum(p.stat().st_size for p in exports.glob("export_*.ply")),
        "training_process_seconds": round(process_seconds, 4),
        "progress_source": "brush_refinement_log",
        "gaussians": count,
        "steps": steps,
        **evaluation,
        "color_model": "SH3",
        "preview_color_model": "SH3 (PLY)",
        "splat_color_model": "SH0",
        "resolution": config["resolution"],
        "evaluation_mode": "holdout" if holdout else "all",
        "thumbnail_source": "evaluation_render" if rendered else "input_photo",
        "note": (
            "每 8 张留出评估；这些图像参与过上游 SfM。"
            if holdout
            else "最终模型使用全部已定位照片；没有独立留出评估指标。"
        )
        + "网页 PLY 预览保留高阶 SH；下载 splat 只保留 SH0。",
    }
