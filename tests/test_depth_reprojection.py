import numpy as np
import pytest

from scripts.benchmark_depth_reprojection import support_mask


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
