import numpy as np
import torch

from webscan.gaussian import export_gaussians, render_reference, logit
from webscan.reconstruction import normalize_scene


def test_alpha_compositing_and_gradients():
    means = torch.tensor([[0.0, 0.0, 2.0], [0.0, 0.0, 3.0]], requires_grad=True)
    scales = torch.full((2, 3), -1.2, requires_grad=True)
    quats = torch.tensor([[1.0, 0, 0, 0], [1.0, 0, 0, 0]], requires_grad=True)
    opa = torch.zeros(2, requires_grad=True)
    color = logit([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]).requires_grad_()
    K = torch.tensor([[20.0, 0, 8.5], [0, 20.0, 8.5], [0, 0, 1.0]])
    image = render_reference(means, scales, quats, opa, color, torch.eye(4), K, 16, 16)
    # At the center: near red alpha .5, far blue alpha .5 behind half transmittance.
    assert torch.allclose(
        image[8, 8], torch.tensor([0.49975, 0.00075, 0.25025]), atol=0.003
    )
    image.square().mean().backward()
    for value in (means, scales, quats, opa, color):
        assert value.grad is not None and torch.isfinite(value.grad).all()


def test_behind_camera_and_export(tmp_path):
    means = torch.tensor([[0.0, 0.0, -2.0]])
    scales = torch.full((1, 3), -2.0)
    quat = torch.tensor([[1.0, 0, 0, 0]])
    K = torch.tensor([[20.0, 0, 8.0], [0, 20.0, 8.0], [0, 0, 1.0]])
    image = render_reference(
        means, scales, quat, torch.zeros(1), torch.zeros(1, 3), torch.eye(4), K, 16, 16
    )
    assert image.max() == 0
    export_gaussians(
        tmp_path, means, scales.exp(), quat, torch.ones(1) * 0.5, torch.ones(1, 3) * 0.5
    )
    assert (tmp_path / "preview.splat").stat().st_size == 32
    ply = (tmp_path / "gaussians.ply").read_bytes()
    assert b"property float f_dc_0" in ply and b"element vertex 1" in ply
    assert len(ply.split(b"end_header\n")[1]) == 17 * 4


def test_scene_normalization_keeps_image_projection():
    points = np.array([[1, 2, 4], [2, 2, 5], [3, 2, 4]], dtype=np.float32)
    original = points.copy()
    view = np.eye(4, dtype=np.float32)
    view[:3, 3] = [0.5, 0.2, 1]
    views = [{"view": view.tolist()}]
    normalized, _ = normalize_scene(points, views)
    before = original @ view[:3, :3].T + view[:3, 3]
    after = (
        normalized @ np.array(views[0]["view"])[:3, :3].T
        + np.array(views[0]["view"])[:3, 3]
    )
    assert np.allclose(before[:, :2] / before[:, 2:], after[:, :2] / after[:, 2:])
