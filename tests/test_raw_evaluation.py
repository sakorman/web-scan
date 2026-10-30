import numpy as np

from webscan.raw_evaluation import (
    bilinear_sample,
    masked_metrics,
    raw_pixel_centers,
    scaled_size,
    warp_render_to_raw,
)


class PinholeCamera:
    def cam_from_img(self, pixels):
        return np.asarray(pixels, dtype=np.float64) - 0.5


def test_scaled_size_and_corner_based_pixel_centers():
    assert scaled_size(1600, 1067, 512) == (512, 341)
    assert scaled_size(12, 8, 512) == (12, 8)
    centers = raw_pixel_centers(4, 3, 4, 3)
    assert np.allclose(centers[0, 0], [0.5, 0.5])
    assert np.allclose(centers[-1, -1], [3.5, 2.5])


def test_bilinear_sampling_keeps_last_pixel_and_masks_outside_coordinates():
    image = np.arange(2 * 3 * 3, dtype=np.float64).reshape(2, 3, 3)
    sampled, valid = bilinear_sample(
        image,
        np.array([[0.0, 2.0, 2.1]]),
        np.array([[0.0, 1.0, 1.0]]),
    )
    assert valid.tolist() == [[True, True, False]]
    assert np.allclose(sampled[0, 0], image[0, 0])
    assert np.allclose(sampled[0, 1], image[1, 2])
    assert np.allclose(sampled[0, 2], 0)


def test_identity_pinhole_warp_and_masked_metrics():
    render = np.linspace(0, 1, 13 * 12 * 3).reshape(12, 13, 3)
    warped, mask = warp_render_to_raw(
        render,
        original_size=(13, 12),
        camera=PinholeCamera(),
        calibration=np.array([[1, 0, 0.5], [0, 1, 0.5], [0, 0, 1]]),
        undistorted_size=(13, 12),
        output_size=(13, 12),
    )
    assert mask.all()
    assert np.allclose(warped, render)
    metrics = masked_metrics(warped, render, mask)
    assert metrics["psnr"] == 120.0
    assert metrics["ssim"] == 1.0
