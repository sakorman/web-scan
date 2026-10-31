import numpy as np
from PIL import Image
import pytest

from scripts.compare_quality import ReferenceMetrics, metrics


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
    assert result['ssim'] == pytest.approx(dense_reference(a, b), abs=2e-6)
    expected_psnr = -10 * np.log10(np.mean(((a.astype(float) - b) / 255) ** 2))
    assert result['psnr'] == pytest.approx(expected_psnr, abs=1e-12)
    assert metrics(Image.fromarray(a), Image.fromarray(a)) == {'psnr': 120.0, 'ssim': 1.0}


def test_flat_bright_channels_keep_luminance_and_contrast_terms():
    a = np.full((31, 39, 3), 254, dtype=np.uint8)
    b = a.copy()
    b[:, :, 0] = 253
    b[::2, ::2, 1] = 252
    # Float32 second-moment subtraction is ill-conditioned near solid white.
    # The unchanged dense baseline differs from float64 by 5.72e-5 here;
    # separate frozen-output comparisons require exact baseline agreement.
    assert metrics(Image.fromarray(a), Image.fromarray(b))['ssim'] == pytest.approx(
        dense_reference(a, b), abs=1e-4
    )


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
