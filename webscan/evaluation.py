"""Validate the complete holdout output of the pinned Brush COLMAP pipeline."""

import hashlib
import math
import re
from pathlib import Path

from PIL import Image, UnidentifiedImageError


def expected_views(views):
    paths = sorted((Path(view["image"]) for view in views), key=lambda path: path.name)
    if not paths or len({path.stem for path in paths}) != len(paths):
        raise ValueError("评测输入为空或图片名称重复")
    return paths


def validate_render_set(views, folder, resolution):
    """Check names, full image decoding and training-size aspect ratio, not brightness."""
    references = expected_views(views)[::8]
    expected = {path.stem + ".png" for path in references}
    actual = {path.name for path in folder.glob("*.png")}
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"评测图片不完整：缺少 {len(missing)} 张，多出 {len(extra)} 张"
        )
    records, rendered = [], []
    for reference in references:
        path = folder / (reference.stem + ".png")
        try:
            with Image.open(reference) as source:
                width, height = source.size
            with Image.open(path) as image:
                if image.format != "PNG":
                    raise ValueError("评测图片格式不是 PNG")
                image.verify()
            with Image.open(path) as image:
                image.load()
                render_width, render_height = image.size
        except (OSError, UnidentifiedImageError, SyntaxError) as error:
            raise ValueError(f"无法完整读取评测图片：{path.name}") from error
        longest = min(resolution, max(width, height))
        if max(render_width, render_height) != longest or abs(
            render_width * height - render_height * width
        ) > max(width, height):
            raise ValueError(f"评测图片尺寸或画幅不符：{path.name}")
        records.append(
            {
                "name": path.name,
                "width": render_width,
                "height": render_height,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "reference_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
            }
        )
        rendered.append(path)
    return records, rendered


def validate_evaluation(scene, exports, config, text):
    paths = expected_views(scene["views"])
    holdout = config.get("evaluation_mode", "holdout") == "holdout"
    expected_count = len(paths[::8]) if holdout else 0
    counts = re.findall(
        r"\bbrush_cli\] Loaded dataset with (\d+) training, (\d+) eval views", text
    )
    if not counts or tuple(map(int, counts[-1])) != (
        len(paths) - expected_count,
        expected_count,
    ):
        raise ValueError("训练/评测视图数量与已定位照片不一致")
    summary = {
        "training_views": len(paths) - expected_count,
        "evaluation_views": expected_count,
        "evaluation": None,
        "evaluation_verified": False,
        "evaluation_images": [],
    }
    if not holdout:
        return summary, []
    results = re.findall(r"\bbrush_cli\] Eval iter (\d+): PSNR (\S+), ssim (\S+)", text)
    steps = config["steps"]
    if not results or int(results[-1][0]) != steps:
        raise ValueError(f"评测未完成：缺少第 {steps} 步的最终结果")
    try:
        psnr, ssim = map(float, results[-1][1:])
    except ValueError as error:
        raise ValueError("评测指标不是有效数值") from error
    if not math.isfinite(psnr) or not math.isfinite(ssim) or not -1 <= ssim <= 1:
        raise ValueError("评测指标非有限或超出有效范围")
    records, rendered = validate_render_set(
        scene["views"], exports / f"eval_{steps}", config["resolution"]
    )
    summary.update(
        evaluation={"psnr": psnr, "ssim": ssim},
        evaluation_verified=True,
        evaluation_images=records,
    )
    return summary, rendered
