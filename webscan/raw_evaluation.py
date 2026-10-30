"""Utilities for evaluating renders in the original, distorted photo domain."""

from __future__ import annotations

import math

import numpy as np


def scaled_size(width, height, long_edge):
    """Return a non-upscaled size with ``long_edge`` as its maximum side."""
    if min(width, height, long_edge) < 1:
        raise ValueError("图像尺寸和评分长边必须为正数")
    scale = min(1.0, long_edge / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def raw_pixel_centers(width, height, output_width, output_height):
    """Return output-pixel centers in COLMAP's corner-based source coordinates."""
    if min(width, height, output_width, output_height) < 1:
        raise ValueError("图像尺寸必须为正数")
    rows, columns = np.indices((output_height, output_width), dtype=np.float64)
    return np.stack(
        [
            (columns + 0.5) * width / output_width,
            (rows + 0.5) * height / output_height,
        ],
        axis=-1,
    )


def bilinear_sample(image, x, y):
    """Sample an RGB image at zero-based array coordinates without extrapolation."""
    image = np.asarray(image, dtype=np.float64)
    if image.ndim != 3 or image.shape[2] != 3 or min(image.shape[:2]) < 2:
        raise ValueError("采样图像必须是至少 2×2 的 RGB 图")
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    if x.shape != y.shape:
        raise ValueError("采样坐标形状不一致")
    height, width = image.shape[:2]
    valid = (
        np.isfinite(x)
        & np.isfinite(y)
        & (x >= 0)
        & (y >= 0)
        & (x <= width - 1)
        & (y <= height - 1)
    )
    safe_x = np.where(np.isfinite(x), x, 0.0)
    safe_y = np.where(np.isfinite(y), y, 0.0)
    x0 = np.clip(np.floor(safe_x).astype(np.int64), 0, width - 2)
    y0 = np.clip(np.floor(safe_y).astype(np.int64), 0, height - 2)
    dx = (safe_x - x0)[..., None]
    dy = (safe_y - y0)[..., None]
    sampled = (
        (1 - dx) * (1 - dy) * image[y0, x0]
        + dx * (1 - dy) * image[y0, x0 + 1]
        + (1 - dx) * dy * image[y0 + 1, x0]
        + dx * dy * image[y0 + 1, x0 + 1]
    )
    sampled[~valid] = 0
    return sampled, valid


def warp_render_to_raw(
    render,
    original_size,
    camera,
    calibration,
    undistorted_size,
    output_size,
):
    """Warp an undistorted pinhole render into its source-photo pixel domain.

    ``camera`` is the original COLMAP camera.  Its ``cam_from_img`` inverse
    projection removes the source distortion.  The normalized rays then map to
    the pinhole calibration stored with the undistorted training view.  The
    returned mask excludes coordinates outside the rendered image instead of
    inventing edge pixels.
    """
    source_width, source_height = original_size
    output_width, output_height = output_size
    undistorted_width, undistorted_height = undistorted_size
    calibration = np.asarray(calibration, dtype=np.float64)
    if calibration.shape != (3, 3):
        raise ValueError("去畸变相机内参必须是 3×3 矩阵")
    if min(undistorted_width, undistorted_height) < 1:
        raise ValueError("去畸变图像尺寸必须为正数")
    source_pixels = raw_pixel_centers(
        source_width, source_height, output_width, output_height
    )
    normalized = np.asarray(camera.cam_from_img(source_pixels.reshape(-1, 2)))
    if normalized.shape != (source_pixels.size // 2, 2):
        raise ValueError("COLMAP 未返回每个原始像素的相机射线")
    pinhole_x = normalized[:, 0] * calibration[0, 0] + calibration[0, 2]
    pinhole_y = normalized[:, 1] * calibration[1, 1] + calibration[1, 2]
    render = np.asarray(render, dtype=np.float64)
    if render.ndim != 3 or render.shape[2] != 3:
        raise ValueError("渲染图必须是 RGB 图")
    render_x = (
        pinhole_x.reshape(output_height, output_width)
        * render.shape[1]
        / undistorted_width
        - 0.5
    )
    render_y = (
        pinhole_y.reshape(output_height, output_width)
        * render.shape[0]
        / undistorted_height
        - 0.5
    )
    return bilinear_sample(render, render_x, render_y)


def masked_metrics(render, reference, mask):
    """Compute PSNR and SSIM only where both camera-model warps are valid."""
    render = np.asarray(render, dtype=np.float64)
    reference = np.asarray(reference, dtype=np.float64)
    mask = np.asarray(mask, dtype=bool)
    if render.shape != reference.shape or render.ndim != 3 or render.shape[2] != 3:
        raise ValueError("渲染与参考图必须是同尺寸 RGB 图")
    if mask.shape != render.shape[:2]:
        raise ValueError("有效像素掩码尺寸不匹配")
    if min(render.shape[:2]) < 11:
        raise ValueError("共同评分图每边至少需要 11 像素")
    valid_pixels = int(mask.sum())
    if valid_pixels < 11 * 11:
        raise ValueError("共同有效区域不足以计算 SSIM")
    mse = float(np.mean((render[mask] - reference[mask]) ** 2))
    axis = np.arange(11, dtype=np.float64) - 5
    kernel = np.exp(-(axis**2) / (2 * 1.5**2))
    kernel /= kernel.sum()

    def blur(value):
        horizontal = np.apply_along_axis(
            lambda row: np.convolve(row, kernel, mode="valid"), 1, value
        )
        return np.apply_along_axis(
            lambda column: np.convolve(column, kernel, mode="valid"), 0, horizontal
        )

    mean_x, mean_y = blur(render), blur(reference)
    variance_x = blur(render * render) - mean_x * mean_x
    variance_y = blur(reference * reference) - mean_y * mean_y
    covariance = blur(render * reference) - mean_x * mean_y
    ssim = (
        (2 * mean_x * mean_y + 0.01**2)
        * (2 * covariance + 0.03**2)
        / ((mean_x * mean_x + mean_y * mean_y + 0.01**2) * (variance_x + variance_y + 0.03**2))
    ).mean(axis=2)
    complete_windows = np.apply_along_axis(
        lambda row: np.convolve(row, np.ones(11), mode="valid"), 1, mask.astype(np.int16)
    )
    complete_windows = np.apply_along_axis(
        lambda column: np.convolve(column, np.ones(11), mode="valid"), 0, complete_windows
    ) == 121
    valid_windows = int(complete_windows.sum())
    if valid_windows == 0:
        raise ValueError("共同有效区域没有完整的 11×11 SSIM 窗口")
    return {
        "psnr": float(-10 * math.log10(max(mse, 1e-12))),
        "ssim": float(ssim[complete_windows].mean()),
        "valid_pixels": valid_pixels,
        "valid_pixel_fraction": valid_pixels / mask.size,
        "valid_ssim_windows": valid_windows,
    }
