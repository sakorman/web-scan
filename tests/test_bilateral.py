import numpy as np

from scripts.benchmark_bilateral import GRID_SIZE, MAX_SHIFT, correct_image, fit_grid, lookup


def test_grid_interpolates_linear_field_including_borders():
    coords = np.indices((GRID_SIZE,) * 3).reshape(3, -1).T / (GRID_SIZE - 1)
    positions = np.array([[0, 0, 1], [1, 1, 0], [.15, .86, .45], [.7, .2, .9]])
    np.testing.assert_allclose(lookup(coords, positions), positions)


def test_empty_support_keeps_pixels_and_local_fit_is_bounded():
    rng = np.random.default_rng(42)
    pixels = rng.integers(0, 256, (67, 83, 3), dtype=np.uint8)
    empty = fit_grid(np.empty((0, 3)), np.empty((0, 3)))
    np.testing.assert_array_equal(correct_image(pixels, empty), pixels)
    positions = np.zeros((24, 3))
    grid = fit_grid(positions, np.ones_like(positions))
    at_sample, at_far_corner = lookup(grid, np.array([[0, 0, 0], [1, 1, 1]]))
    assert np.all((at_sample > 0) & (at_sample <= MAX_SHIFT))
    np.testing.assert_array_equal(at_far_corner, [0, 0, 0])
