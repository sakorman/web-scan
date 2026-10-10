"""Original, small-scene differentiable Gaussian renderer. No CUDA extension required.

Uses perspective covariance projection and front-to-back alpha compositing. Dense
N x H x W tensors make it deliberately bounded; gsplat is the scalable CUDA option.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import torch


def rotation(quats):
    q = torch.nn.functional.normalize(quats, dim=-1)
    w, x, y, z = q.unbind(-1)
    return torch.stack(
        (
            1 - 2 * (y * y + z * z),
            2 * (x * y - z * w),
            2 * (x * z + y * w),
            2 * (x * y + z * w),
            1 - 2 * (x * x + z * z),
            2 * (y * z - x * w),
            2 * (x * z - y * w),
            2 * (y * z + x * w),
            1 - 2 * (x * x + y * y),
        ),
        -1,
    ).reshape(-1, 3, 3)


def render_reference(
    means, log_scales, quats, opacity_logits, color_logits, view, K, width, height
):
    p = means @ view[:3, :3].T + view[:3, 3]
    order = torch.argsort(p[:, 2].detach())
    p = p[order]
    z = p[:, 2].clamp_min(0.05)
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    u, v = fx * p[:, 0] / z + cx, fy * p[:, 1] / z + cy
    zero = torch.zeros_like(z)
    J = torch.stack(
        (
            fx / z,
            zero,
            -fx * p[:, 0] / z.square(),
            zero,
            fy / z,
            -fy * p[:, 1] / z.square(),
        ),
        -1,
    ).reshape(-1, 2, 3)
    R = rotation(quats[order])
    S = log_scales[order].exp()
    covariance = (R * S[:, None, :]) @ (R * S[:, None, :]).transpose(1, 2)
    camera_cov = view[:3, :3] @ covariance @ view[:3, :3].T
    cov2 = (
        J @ camera_cov @ J.transpose(1, 2)
        + torch.eye(2, device=means.device)[None] * 0.3
    )
    a, b, c = cov2[:, 0, 0], cov2[:, 0, 1], cov2[:, 1, 1]
    det = (a * c - b * b).clamp_min(1e-8)
    yy, xx = torch.meshgrid(
        torch.arange(height, device=means.device) + 0.5,
        torch.arange(width, device=means.device) + 0.5,
        indexing="ij",
    )
    dx, dy = xx[None] - u[:, None, None], yy[None] - v[:, None, None]
    distance = (
        c[:, None, None] * dx.square()
        - 2 * b[:, None, None] * dx * dy
        + a[:, None, None] * dy.square()
    ) / det[:, None, None]
    visible = (p[:, 2] > 0.05).to(means.dtype)
    alpha = (
        opacity_logits[order].sigmoid()[:, None, None]
        * torch.exp(-0.5 * distance.clamp_min(0))
        * visible[:, None, None]
    ).clamp_max(0.99)
    trans = torch.cumprod(
        torch.cat((torch.ones_like(alpha[:1]), 1 - alpha + 1e-7), 0), dim=0
    )[:-1]
    weights = alpha * trans
    rgb = torch.einsum("nhw,nc->hwc", weights, color_logits[order].sigmoid())
    return rgb.clamp(0, 1)


def render_cuda(
    means, log_scales, quats, opacity_logits, color_logits, view, K, width, height
):
    from gsplat import rasterization

    colors, _, _ = rasterization(
        means=means,
        quats=torch.nn.functional.normalize(quats, dim=-1),
        scales=log_scales.exp(),
        opacities=opacity_logits.sigmoid(),
        colors=color_logits.sigmoid(),
        viewmats=view[None],
        Ks=K[None],
        width=width,
        height=height,
        packed=False,
    )
    return colors[0]


def logit(value):
    return torch.logit(torch.as_tensor(value, dtype=torch.float32).clamp(0.001, 0.999))


def export_gaussians(path: Path, means, scales, quats, opacities, colors):
    """Write standard degree-0 Gaussian PLY and the documented 32-byte splat format."""
    path.mkdir(parents=True, exist_ok=True)
    arrays = [
        v.detach().cpu().numpy() if isinstance(v, torch.Tensor) else v
        for v in (means, scales, quats, opacities, colors)
    ]
    xyz, scale, quat, opacity, rgb = arrays
    quat = quat / np.maximum(np.linalg.norm(quat, axis=1, keepdims=True), 1e-8)
    fields = [
        "x",
        "y",
        "z",
        "nx",
        "ny",
        "nz",
        "f_dc_0",
        "f_dc_1",
        "f_dc_2",
        "opacity",
        "scale_0",
        "scale_1",
        "scale_2",
        "rot_0",
        "rot_1",
        "rot_2",
        "rot_3",
    ]
    data = np.empty(len(xyz), dtype=[(f, "<f4") for f in fields])
    packed = np.column_stack(
        [
            xyz,
            np.zeros_like(xyz),
            (rgb - 0.5) / 0.28209479177387814,
            np.log(
                np.clip(opacity, 1e-6, 1 - 1e-6)
                / (1 - np.clip(opacity, 1e-6, 1 - 1e-6))
            ),
            np.log(np.maximum(scale, 1e-8)),
            quat,
        ]
    )
    for i, field in enumerate(fields):
        data[field] = packed[:, i]
    header = (
        "ply\nformat binary_little_endian 1.0\ncomment Web Scan degree-0 Gaussian model\nelement vertex "
        + str(len(xyz))
        + "\n"
        + "".join("property float " + f + "\n" for f in fields)
        + "end_header\n"
    )
    with (path / "gaussians.ply").open("wb") as f:
        f.write(header.encode())
        f.write(data.tobytes())
    splat = np.zeros(
        len(xyz),
        dtype=[
            ("xyz", "<f4", 3),
            ("scale", "<f4", 3),
            ("rgba", "u1", 4),
            ("quat", "u1", 4),
        ],
    )
    splat["xyz"], splat["scale"] = xyz, scale
    splat["rgba"] = np.clip(np.column_stack([rgb, opacity]) * 255, 0, 255).astype("u1")
    splat["quat"] = np.clip(quat * 128 + 128, 0, 255).astype("u1")
    (path / "preview.splat").write_bytes(splat.tobytes())


def train_scene(scene, output, *, steps, max_points, resolution, backend, progress):
    torch.set_num_threads(4)
    torch.manual_seed(7)
    device = torch.device("cuda" if backend == "cuda" else "cpu")
    if backend == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA 后端需要 NVIDIA GPU；请使用本地参考模式。")
    renderer = render_cuda if backend == "cuda" else render_reference
    rng = np.random.default_rng(7)
    xyz, colors = scene["points"], scene["colors"]
    indices = rng.choice(len(xyz), min(len(xyz), max_points), replace=False)
    xyz, colors = xyz[indices], colors[indices]
    means = torch.nn.Parameter(torch.tensor(xyz, dtype=torch.float32, device=device))
    with torch.no_grad():
        dist = torch.cdist(means, means)
        dist.fill_diagonal_(float("inf"))
        size = dist.min(1).values.clamp(0.008, 0.2) * 0.55
    scales = torch.nn.Parameter(size[:, None].expand(-1, 3).log().clone())
    quat = torch.nn.Parameter(
        torch.tensor([[1.0, 0, 0, 0]], device=device).repeat(len(xyz), 1)
    )
    opacity = torch.nn.Parameter(logit(torch.full((len(xyz),), 0.65)).to(device))
    color = torch.nn.Parameter(logit(colors).to(device))
    optim = torch.optim.Adam(
        [
            {"params": [means], "lr": 0.001},
            {"params": [scales], "lr": 0.01},
            {"params": [quat], "lr": 0.002},
            {"params": [opacity], "lr": 0.025},
            {"params": [color], "lr": 0.025},
        ]
    )
    from PIL import Image

    views = []
    for item in scene["views"]:
        img = Image.open(item["image"]).convert("RGB")
        factor = resolution / max(img.size)
        w, h = max(8, round(img.width * factor)), max(8, round(img.height * factor))
        K = np.array(item["K"], dtype=np.float32).copy()
        K[0] *= w / img.width
        K[1] *= h / img.height
        target = (
            torch.from_numpy(
                np.array(img.resize((w, h), Image.Resampling.LANCZOS)).copy()
            )
            .float()
            .to(device)
            / 255
        )
        views.append(
            (
                target,
                torch.tensor(item["view"], dtype=torch.float32, device=device),
                torch.tensor(K, device=device),
                w,
                h,
            )
        )
    losses = []
    start_error = None
    with torch.no_grad():
        target, view, K, w, h = views[0]
        start_error = float(
            torch.mean(
                (renderer(means, scales, quat, opacity, color, view, K, w, h) - target)
                ** 2
            )
        )
    for step in range(steps):
        target, view, K, w, h = views[step % len(views)]
        optim.zero_grad(set_to_none=True)
        image = renderer(means, scales, quat, opacity, color, view, K, w, h)
        loss = (image - target).abs().mean() + 0.002 * scales.exp().mean()
        if not torch.isfinite(loss):
            raise RuntimeError("训练出现非有限数值，请检查输入图像和位姿。")
        loss.backward()
        torch.nn.utils.clip_grad_norm_([means, scales, quat, opacity, color], 2.0)
        optim.step()
        with torch.no_grad():
            scales.clamp_(math.log(0.002), math.log(0.5))
            opacity.clamp_(-7, 7)
        losses.append(float(loss.detach()))
        if step % 5 == 0 or step == steps - 1:
            progress(step + 1, steps, losses[-1])
    with torch.no_grad():
        target, view, K, w, h = views[0]
        image = renderer(means, scales, quat, opacity, color, view, K, w, h)
        end_error = float(((image - target) ** 2).mean())
        output.mkdir(parents=True, exist_ok=True)
        Image.fromarray((image.cpu().numpy() * 255).astype("uint8")).save(
            output / "thumbnail.jpg"
        )
        export_gaussians(
            output, means, scales.exp(), quat, opacity.sigmoid(), color.sigmoid()
        )
    return {
        "gaussians": len(xyz),
        "steps": steps,
        "training_views": len(views),
        "backend": backend,
        "first_view_mse_before": start_error,
        "first_view_mse_after": end_error,
        "final_loss": losses[-1],
        "loss_history": losses,
        "color_model": "SH0",
        "note": "训练视角误差用于调试，不代表独立视角质量；固定高斯数量，无稠密化。",
    }
