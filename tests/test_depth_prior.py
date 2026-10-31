import numpy as np

from scripts.benchmark_depth_prior import analyze_view, camera_depth, fit_cells, sample_depth


def fixture():
    y, x = np.mgrid[:16, :16]
    prediction = (x + y * 0.3 + 5).astype(float)
    xy = np.column_stack([x.ravel() + 0.5, y.ravel() + 0.5])
    z = 1 / (0.2 * prediction.ravel() + 0.7)
    return prediction, xy, z


def test_camera_depth_uses_transformed_z_instead_of_world_z_or_ray_distance():
    pose = np.array([[0, 0, 1, 2], [0, 1, 0, 0], [-1, 0, 0, 5]])
    assert camera_depth(pose, np.array([2, 3, 4])) == 3


def test_pixel_centers_and_last_pixel_are_not_shifted_or_padded():
    depth = np.array([[1, 3], [5, 7]])
    values, valid = sample_depth(depth, [[0.5, 0.5], [1, 1], [1.5, 1.5], [0.4, 1], [2, 1]])
    assert valid.tolist() == [True, True, True, False, False]
    assert values[:3].tolist() == [1, 4, 7]
    assert np.isnan(values[3:]).all()


def test_recovers_known_inverse_depth_scale_and_shift():
    row = analyze_view(*fixture())
    assert row["passed"]
    assert np.isclose(row["scale"], 0.2) and np.isclose(row["shift"], 0.7)
    assert row["check"]["p90_abs_rel"] < 1e-12
    assert row["constant_depth_check"]["p90_abs_rel"] > 0.2
    assert row["fit_anchors"] == row["check_anchors"] == 128


def test_check_regions_do_not_influence_fit_and_can_reject_it():
    pred, xy, z = fixture()
    fit = fit_cells(xy, 16, 16)
    z[~fit] *= 2
    row = analyze_view(pred, xy, z)
    assert np.isclose(row["scale"], 0.2) and np.isclose(row["shift"], 0.7)
    assert np.isclose(row["check"]["median_abs_rel"], 0.5)
    assert not row["passed"]


def test_constant_and_reversed_predictions_are_rejected():
    pred, xy, z = fixture()
    assert analyze_view(np.ones_like(pred), xy, z)["reason"] == "degenerate_fit"
    assert analyze_view(-pred, xy, z)["reason"] == "nonpositive_scale"


def test_insufficient_anchors_and_invalid_dense_regions_are_rejected():
    pred, xy, z = fixture()
    assert analyze_view(pred, xy[:10], z[:10])["reason"] == "insufficient_spatial_anchors"
    # Remove anchors only in the damaged patch: perfect check fit still cannot pass.
    keep = (xy[:, 0] >= 4) | (xy[:, 1] >= 4)
    pred[:4, :4] = -100
    row = analyze_view(pred, xy[keep], z[keep])
    assert row["check"]["p90_abs_rel"] < 1e-12
    assert row["invalid_dense_fraction"] == 1/16
    assert not row["passed"]


def test_invalid_check_depth_is_counted_without_silent_scoring_exclusion():
    pred, xy, z = fixture()
    pred[0, 4] = -100  # Odd cell, held out from fitting.
    row = analyze_view(pred, xy, z)
    assert row["reason"] == "invalid_check_depth"
    assert row["invalid_check_anchors"] == 1
    assert "check" not in row
