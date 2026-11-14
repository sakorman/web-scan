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


def test_error_tiles_locate_one_channel_error_and_recover_whole_frame_mse():
    reference = Image.new('RGB', (17, 13))
    rendered = reference.copy()
    rendered.putpixel((16, 12), (0, 255, 0))
    scorer = ReferenceMetrics(reference)
    measured = scorer.score(rendered, error_grid=4)
    tiles = measured.pop('error_tiles')
    assert measured == scorer.score(rendered)
    counts = np.zeros((13, 17), dtype=int)
    error_sum = 0
    for tile in tiles:
        x0, y0, x1, y1 = tile['xyxy']
        counts[y0:y1, x0:x1] += 1
        weighted = tile['mse'] * (x1 - x0) * (y1 - y0)
        assert weighted == pytest.approx(1/3 if x0 <= 16 < x1 and y0 <= 12 < y1 else 0)
        error_sum += weighted
    assert len(tiles) == 16 and np.all(counts == 1)
    assert error_sum / counts.size == pytest.approx(10 ** (-measured['psnr'] / 10))


@pytest.mark.parametrize('divisions', [0, 14, 17])
def test_error_grid_rejects_empty_or_oversized_tiles(divisions):
    image = Image.new('RGB', (17, 13))
    with pytest.raises(ValueError, match='grid'):
        ReferenceMetrics(image).score(image, error_grid=divisions)
