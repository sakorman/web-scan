import copy
import json
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.benchmark_depth_consistency import check_pair
from scripts.benchmark_gmdepth_cycle import (assert_scores_close, calibration,
                                              load_prediction, reverse_pair, score_regions)
from scripts.benchmark_depth_prior import digest
from scripts.benchmark_gmdepth import UPSTREAM, WEIGHTS_SHA256


def pair():
    views = []
    for name, x, z in [('a', 0, 3), ('b', -.2, 6)]:
        pose = np.eye(4); pose[0, 3] = x
        views.append(dict(name=name, pose=pose.tolist(), size=[100, 80],
                          K=[[100, 0, 50], [0, 100, 40], [0, 0, 1]],
                          xy=[[1, 1]]*24+[[30, 1]], z=[z]*24+[999]))
    return dict(source=views[0], target=views[1], size=[100, 80])


def test_reverse_uses_target_fit_depth_and_inverse_pose_without_mutation():
    row = pair(); original = copy.deepcopy(row)
    rev = reverse_pair(row)
    assert row == original
    assert rev['source'] == row['target'] and rev['target'] == row['source']
    assert rev['near'] == 3 and rev['far'] == 12  # 999 is in check, not fit.
    assert np.allclose(rev['pose'], [[1, 0, 0, .2], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])


def test_reverse_rejects_insufficient_fit_anchors():
    row = pair(); row['target']['xy'] = [[30, 1]]*25
    with pytest.raises(ValueError, match='fit anchors'):
        reverse_pair(row)


@pytest.mark.parametrize('target_z, category', [(4, 'supported'), (2, 'occlusion_candidate'), (8, 'front_depth_conflict')])
def test_cycle_planar_depth_classification(target_z, category):
    row = pair(); xy = np.array([[50.5, 40.5]])
    masks = check_pair(xy, np.array([4.]), calibration(row['source'], row['size']),
                       calibration(row['target'], row['size']), np.full((80, 100), target_z),
                       dict(relative_depth_tolerance=.05, roundtrip_pixels=2, min_parallax_degrees=1))
    assert masks[category].tolist() == [True]
    assert sum(int(v[0]) for v in masks.values()) == 1
    # The two predictions can agree on 4 even when the actual plane is at 3.
    if target_z == 4:
        assert abs(4/3-1) > .25


def test_calibration_keeps_scaled_corner_rays():
    row = pair(); intrinsics, _ = calibration(row['source'], [50, 40])
    assert np.allclose(intrinsics, [50, 50, 25, 20])


def test_regions_include_missing_truth_and_invalid_predictions():
    scores = score_regions(np.array([2., np.nan, 3., 4.]), np.ones(4), np.array([2., 2., np.nan, 4.]),
                           np.array([True, True, True, False]), np.array([True, False, True, True]))
    assert scores['all']['gmdepth']['reference_samples'] == 2
    assert scores['rejected']['gmdepth']['invalid_predictions'] == 1
    assert scores['retained']['gmdepth']['missing_reference'] == 1
    assert scores['retained']['grid_samples'] == 2


def test_score_comparison_allows_reduction_roundoff():
    assert_scores_close({'a': {'b': 1.0}}, {'a': {'b': 1.0 + 1e-13}})
    with pytest.raises(AssertionError):
        assert_scores_close({'a': {'b': 1.0}}, {'a': {'b': 1.0 + 1e-5}})


def test_prediction_loader_rejects_changed_bytes_and_duplicate_identity(tmp_path):
    pairs = tmp_path/'pairs.json'; pairs.write_text('{}')
    path = tmp_path/'depth.npy'; np.save(path, np.ones((8, 10)))
    view = dict(name='a', depth=path.name, sha256=digest(path))
    record = dict(status='complete', pairs_sha256=digest(pairs), upstream=UPSTREAM,
                  weights_sha256=WEIGHTS_SHA256, views=[view])
    meta = tmp_path/'inference.json'; meta.write_text(json.dumps(record))
    assert load_prediction(tmp_path, pairs, 'a', [10, 8])[0].shape == (8, 10)
    record['views'] = [view, view]; meta.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='duplicate'):
        load_prediction(tmp_path, pairs, 'a', [10, 8])
    record['views'] = [view]; meta.write_text(json.dumps(record)); np.save(path, np.zeros((8, 10)))
    with pytest.raises(ValueError, match='bytes changed'):
        load_prediction(tmp_path, pairs, 'a', [10, 8])
