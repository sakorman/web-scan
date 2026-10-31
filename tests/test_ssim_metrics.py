import numpy as np
from PIL import Image
import pytest
import torch
import torch.nn.functional as F

from scripts.compare_quality import ReferenceMetrics, metrics


def legacy_ssim(a, b):
    """Frozen pre-cache Float32 calculation for same-backend compatibility."""
    x = torch.tensor((a.astype(np.float64) / 255).transpose(2, 0, 1)[None], dtype=torch.float32)
    y = torch.tensor((b.astype(np.float64) / 255).transpose(2, 0, 1)[None], dtype=torch.float32)
    axis = torch.arange(11, dtype=torch.float32) - 5
    kernel = torch.exp(-(axis**2) / (2 * 1.5**2))
    kernel /= kernel.sum()
    window = (kernel[:, None] * kernel[None, :]).expand(3, 1, 11, 11)

    def blur(value):
        return F.conv2d(value, window, groups=3)

    mx, my = blur(x), blur(y)
    vx, vy = blur(x * x) - mx * mx, blur(y * y) - my * my
    cov = blur(x * y) - mx * my
    return float(((2 * mx * my + .01**2) * (2 * cov + .03**2)
                  / ((mx * mx + my * my + .01**2) * (vx + vy + .03**2))).mean())


def dense_reference(a, b):
    """Independent float64, valid-window SSIM oracle without Torch convolutions."""
    a, b = a.astype(np.float64) / 255, b.astype(np.float64) / 255
    offsets = np.arange(-5, 6)
    weights = np.exp(-(offsets[:, None] ** 2 + offsets[None, :] ** 2) / (2 * 1.5**2))
    weights /= weights.sum()

    def average(image):
        windows = np.lib.stride_tricks.sliding_window_view(image, (11, 11), axis=(0, 1))
        return np.einsum('hwcij,ij->hwc', windows, weights)

    x, y = average(a), average(b)
    vx, vy = average(a * a) - x * x, average(b * b) - y * y
    cov = average(a * b) - x * y
    return np.mean((2 * x * y + .01**2) * (2 * cov + .03**2)
                   / ((x * x + y * y + .01**2) * (vx + vy + .03**2)))


@pytest.mark.parametrize('shape', [(11, 11, 3), (37, 54, 3), (54, 37, 3)])
def test_rgb_ssim_matches_dense_float64_oracle(shape):
    rng = np.random.default_rng(42)
    a = rng.integers(0, 256, shape, dtype=np.uint8)
    b = np.roll(a, 1, axis=1)
    result = metrics(Image.fromarray(a), Image.fromarray(b))
    assert result['ssim'] == legacy_ssim(a, b)
    assert result['ssim'] == pytest.approx(dense_reference(a, b), abs=2e-6)
    expected_psnr = -10 * np.log10(np.mean(((a.astype(float) - b) / 255) ** 2))
    assert result['psnr'] == pytest.approx(expected_psnr, abs=1e-12)
    assert metrics(Image.fromarray(a), Image.fromarray(a)) == {'psnr': 120.0, 'ssim': 1.0}


def test_flat_bright_channels_keep_legacy_float32_result():
    a = np.full((31, 39, 3), 254, dtype=np.uint8)
    b = a.copy()
    b[:, :, 0] = 253
    b[::2, ::2, 1] = 252
    # Near-white second-moment subtraction has backend-dependent roundoff.
    # Require exact agreement with the previous implementation on this backend;
    # the textured cases above separately check the independent Float64 formula.
    assert metrics(Image.fromarray(a), Image.fromarray(b))['ssim'] == legacy_ssim(a, b)


def test_paired_renders_reuse_only_their_own_reference_without_mutation():
    rng = np.random.default_rng(19)
    reference = Image.fromarray(rng.integers(0, 256, (41, 53, 3), dtype=np.uint8))
    renders = [reference, Image.new('RGB', reference.size, 'black'),
               Image.new('RGB', reference.size, 'white')]
    scorer = ReferenceMetrics(reference)
    expected = [metrics(render, reference) for render in renders]
    assert [scorer.score(render) for render in renders] == expected
    assert [scorer.score(render) for render in reversed(renders)] == list(reversed(expected))
    other_reference = Image.new('RGB', reference.size, 'white')
    assert ReferenceMetrics(other_reference).score(renders[1]) != expected[1]
