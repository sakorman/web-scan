from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.benchmark_depth_prior import digest, save
from scripts.benchmark_depth_reprojection import (
    audit, calibration, load_sensors, run, sensor_audit, source_triple,
    support_mask, verify_inputs,
)


CONFIG = dict(relative_depth_tolerance=.05, roundtrip_pixels=2., min_parallax_degrees=1.)


def test_support_mask_requires_positive_native_samples_and_tolerance():
    result = support_mask(
        np.array([2.0, 2.0, -1.0, 2.0]),
        np.array([2.08, 2.3, 2.0, np.nan]),
        np.array([True, True, True, True]),
        0.05,
    )
    assert result.tolist() == [True, False, False, False]


def test_support_mask_rejects_mismatched_or_invalid_inputs():
    with pytest.raises(ValueError):
        support_mask(np.ones(2), np.ones(1), np.ones(2, dtype=bool), 0.05)
    with pytest.raises(ValueError):
        support_mask(np.ones(1), np.ones(1), np.ones(1, dtype=bool), 0)

def scene():
    # All three projections lie in the same held-out checker cell, on a Z=5 plane.
    views = [dict(camera_model='PINHOLE', camera_params=[10, 10, 40.5, 20.5],
                  width=64, height=64, cam_from_world=np.c_[np.eye(3), [-x, 0, 0]])
             for x in (0, -1, 1)]
    depths = [np.full((64, 64), 5.) for _ in views]
    sensors = [np.full((64, 64), 25000, np.uint16) for _ in views]
    return np.array([[0., 0., 5.]]), np.array([0]), views, depths, sensors, np.array([[0., 0., 50.]])


def test_true_plane_has_same_three_rgb_and_sensor_views():
    points, source, views, depths, sensors, cloud = scene()
    base, _ = audit(points, source, views, depths, CONFIG)
    result, _ = audit(points, source, views, depths, CONFIG, sensors, 1., cloud)
    assert result['joint_far_three'] == result['clean_source_triple'] == 1
    assert all(result[k] == value for k, value in base.items())


def test_loose_two_sensor_votes_do_not_validate_source_or_strict_geometry():
    points, source, views, depths, sensors, cloud = scene()
    sensors[0][:] = 0
    result, _ = audit(points, source, views, depths, CONFIG, sensors, 1., cloud)
    assert result['legacy_sensor_two'] == 1
    assert result['joint_check_three'] == result['joint_far_three'] == 0
    sensors[0][:] = 25000
    sensors[1][:] = 26000  # 4% error passes the old 5% band, fails spatial 1%.
    result, _ = audit(points, source, views, depths, CONFIG, sensors, 1., cloud)
    assert result['legacy_sensor_two'] == result['joint_check_three'] == 1
    assert result['joint_spatial_three'] == 0


def test_fit_cells_and_near_sparse_measurements_cannot_confirm():
    points, source, views, depths, sensors, cloud = scene()
    for view in views:
        view['camera_params'][3] = 40.5  # All projections move into fit cells.
    result, _ = audit(points, source, views, depths, CONFIG, sensors, 1., cloud)
    assert result['legacy_sensor_two'] == 1
    assert result['sensor_fit_other_pairs'] == 2
    assert result['joint_check_three'] == 0
    points, source, views, depths, sensors, _ = scene()
    result, _ = audit(points, source, views, depths, CONFIG, sensors, 1., points)
    assert result['joint_spatial_three'] == 1
    assert result['joint_far_three'] == 0


def test_sensor_availability_does_not_depend_on_prior_validity():
    points, source, views, depths, sensors, cloud = scene()
    depths[1][:] = np.nan
    result, _ = audit(points, source, views, depths, CONFIG, sensors, 1., cloud)
    assert result['sampleable_other_pairs'] == 1
    assert result['sensor_check_other_pairs'] == 2
    assert result['joint_check_three'] == 0


def test_same_source_and_pairwise_parallax_are_required():
    points = np.array([[0., 0., 5.]])
    centers = np.array([[0., 0., 0.], [-1., 0., 0.], [1., 0., 0.], [0., 1., 0.]])
    assert not source_triple(points, np.array([0]), centers, np.array([[False, True, True, True]]))[0]
    centers[2] = centers[1]
    assert not source_triple(points, np.array([0]), centers, np.array([[True, True, True, False]]))[0]


def test_pure_rotation_support_counts_fail_geometry():
    points, source, views, depths, sensors, cloud = scene()
    for view in views:
        view['cam_from_world'][:, 3] = 0
    result, _ = audit(points, source, views, depths, CONFIG, sensors, 1., cloud)
    assert result['legacy_depth_only_two'] == 1
    assert result['source_triple'] == result['joint_far_three'] == 0


def test_sensor_uses_nearest_pixel_center_and_preserves_scale():
    points, _, views, _, sensors, cloud = scene()
    cal, _, lift = calibration(views[0])
    xy = np.array([[40.7, 20.5]])
    # Perfect depth at this pixel has world X=0, not X=0.1 along the query ray.
    point = lift(xy, np.array([5.]), cal)
    flags = sensor_audit(point, xy, np.array([5.]), sensors[0], cal, lift, 1., cloud)
    assert not flags['spatial'][0]
    changed = deepcopy(views[0])
    changed['cam_from_world'][:, 3] *= 7
    cal2, _, lift2 = calibration(changed)
    scaled = sensor_audit(point*7, xy, np.array([35.]), sensors[0], cal2, lift2, 1/7, cloud*7)
    assert np.array_equal(scaled['spatial'], flags['spatial'])


def test_missing_or_wrong_joint_target_cannot_vote():
    points, source, views, depths, sensors, cloud = scene()
    # Sensor support at a view whose RGB depth conflicts must not validate it.
    depths[2][:] = 10
    result, arrays = audit(points, source, views, depths, CONFIG, sensors, 1., cloud)
    assert result['legacy_sensor_two'] == 1
    assert arrays['sensor_spatial'].all()
    assert result['front_conflict_groups'] == 1
    assert result['joint_far_three'] == 0

@pytest.mark.parametrize('changed', ['samples', 'consistency', 'analysis', 'anchors', 'inference'])
def test_input_chain_rejects_swapped_artifacts_before_geometry(tmp_path, changed):
    anchors = tmp_path/'anchors.json'
    analysis = tmp_path/'analysis.json'
    cross = tmp_path/'consistency.json'
    samples = tmp_path/'samples.json'
    groups = tmp_path/'groups.json'
    save(anchors, dict(views=[]))
    a = dict(anchor_sha256=digest(anchors), inference=dict(anchor_sha256=digest(anchors), status='complete'))
    if changed == 'inference':
        a['inference']['anchor_sha256'] = 'wrong'
    save(analysis, a)
    save(cross, dict(status='complete', analysis_sha256=digest(analysis), anchors_sha256=digest(anchors)))
    save(samples, dict(overlap=dict(consistency_sha256=digest(cross))))
    save(groups, dict(samples_sha256=digest(samples)))
    paths = dict(samples=samples, consistency=cross, analysis=analysis, anchors=anchors)
    if changed in paths:
        paths[changed].write_text(paths[changed].read_text()+'\n')
    args = SimpleNamespace(samples=tmp_path, groups=tmp_path, consistency=cross, analysis=analysis, anchors=anchors)
    with pytest.raises(ValueError, match='identity chain'):
        verify_inputs(args)


def test_existing_output_and_partial_sensor_inputs_are_rejected(tmp_path):
    output = tmp_path/'kept.json'
    output.write_text('historical evidence')
    with pytest.raises(FileExistsError):
        run(SimpleNamespace(output=output))
    assert output.read_text() == 'historical evidence'
    with pytest.raises(ValueError, match='together'):
        load_sensors(SimpleNamespace(sensor_manifest=output, sensor_evaluation=None), {}, [])
