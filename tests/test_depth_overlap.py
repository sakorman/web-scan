import numpy as np
import pytest

from scripts.benchmark_depth_overlap import classify, nearest


def test_nearest_uses_all_world_points_including_other_views():
    q = np.array([[0., 0., 10.], [0., 0., 5.]])
    # Same projection can be on a different surface. An off-axis point is nearer in 3D.
    cloud = np.array([[0., 0., 5.], [0.3, 0.4, 10.]])
    z = q[:, 2]
    expected = [.05, 0.]
    assert np.allclose(nearest(q, cloud) / z, expected)
    assert np.allclose(nearest(7*q + 37, 7*cloud + 37) / (7*z), expected)


def test_sensor_only_audits_and_distinguishes_missing_wrong_surface_and_loose_depth():
    d2 = np.array([.02, .02, .02, .02, .02, .01])
    d3 = np.array([.04, .04, .04, .04, .04, .04])
    conflict = np.array([False, False, False, False, True, False])
    geo = classify(.01, d2, d3, conflict)
    measured = np.array([.03, np.nan, .009, .02, .03, .04])
    error = np.array([.003, np.nan, .005, .04, .005, 0])
    offset = np.array([.004, np.nan, .006, .05, .007, 0])
    audited = classify(.01, d2, d3, conflict, measured, error, offset)
    assert all(audited[k] == v for k, v in geo.items())
    assert (geo['both_far'], geo['both_far_no_front_conflict']) == (5, 4)
    assert (audited['clean_sensor_valid'], audited['clean_sensor_far_and_within_10pct'],
            audited['clean_within_spatial_radius']) == (3, 2, 1)
    assert audited['both_within_spatial_radius'] == 2


def test_empty_reference_does_not_prove_everything_is_far():
    with pytest.raises(ValueError, match='finite cloud'):
        nearest(np.ones((1, 2)), np.empty((0, 2)))


def test_pinhole_grid_places_sparse_track_and_rejects_swapped_mask(tmp_path):
    from scripts.benchmark_depth_prior import digest
    from scripts.benchmark_depth_overlap import measure

    # A 2x2 image of a plane at Z=2: only the top-left sample coincides with the seed.
    photo = tmp_path / 'source'
    photo.write_bytes(b'fixed image')
    prediction = tmp_path / 'prior.npy'
    np.save(prediction, np.full((2, 2), .5))
    mask = tmp_path / 'support.npz'
    np.savez(mask, valid=np.ones((2, 2), dtype=bool), support=np.full((2, 2), 2),
             conflict=np.array([[0, 0], [1, 0]]))
    view = dict(name='view', image=photo, image_sha256=digest(photo), width=2, height=2,
                camera_model='PINHOLE', camera_params=[1, 1, .5, .5],
                cam_from_world=np.c_[np.eye(3), np.zeros(3)])
    row = dict(grid_shape=[2, 2], map=mask.name, map_sha256=digest(mask), retained_samples=4)
    args = (view, row, tmp_path, dict(grid_step=1, min_other_views=2), dict(scale=1, shift=0),
            {'view': dict(path=prediction, prediction_sha256=digest(prediction), image_sha256=digest(photo))},
            np.array([[.5, .5]]), np.array([[0., 0., 2.]]), None, None, None)
    captured = []
    baseline = measure(*args)
    assert measure(*args, sample_sink=captured.append) == baseline
    assert captured[0]['grid_index'].tolist() == [0, 1, 2, 3]
    assert np.allclose(captured[0]['world'], [[0, 0, 2], [2, 0, 2], [0, 2, 2], [2, 2, 2]])
    counts = baseline['levels']['0.01']
    assert counts['eligible'] == counts['supported'] == 4
    assert counts['both_far'] == 3 and counts['both_far_no_front_conflict'] == 2
    mask.write_bytes(b'not the frozen map')
    with pytest.raises(ValueError, match='identity'):
        measure(*args)
