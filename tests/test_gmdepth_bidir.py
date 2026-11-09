import numpy as np
import pytest

from scripts.benchmark_depth_prior import digest
from scripts.benchmark_gmdepth_bidir import compare_arrays
from scripts.benchmark_gmdepth_bidir_score import load_map


def test_difference_is_relative_to_the_separate_prediction():
    metrics = compare_arrays(np.array([1., 4.]), np.array([2., 4.]))
    assert metrics == dict(identical=False, mean_relative=.25, max_relative=.5)


@pytest.mark.parametrize('reference', [np.ones((2, 1)), np.array([0., 1.]), np.array([1., np.nan])])
def test_invalid_reference_cannot_be_silently_broadcast_or_averaged(reference):
    with pytest.raises(ValueError, match='same grid'):
        compare_arrays(np.ones(2), reference)


def test_bidirectional_score_requires_both_image_identities_and_actual_bytes(tmp_path):
    path = tmp_path/'map.npy'
    data = np.stack([np.ones((2, 4)), np.full((2, 4), 3.)])
    np.save(path, data)
    row = dict(source=dict(name='a'), target=dict(name='b'), size=[4, 2])
    meta = dict(source='a', target='b', map=path.name, map_sha256=digest(path))
    np.testing.assert_array_equal(load_map(tmp_path, meta, row), data)
    with pytest.raises(ValueError, match='pair identity'):
        load_map(tmp_path, {**meta, 'target':'c'}, row)
    np.save(path, data[::-1])
    with pytest.raises(ValueError, match='bytes'):
        load_map(tmp_path, meta, row)
