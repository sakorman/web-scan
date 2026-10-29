import pytest

from webscan.reconstruction import (
    camera_model_for_mode,
    prefer_shared_attempt,
    shared_retry_reason,
)


def attempt(registered, points, reprojection, total=26):
    return {
        "input_images": total,
        "registered_images": registered,
        "sparse_points": points,
        "reprojection_error": reprojection,
    }


def test_shared_retry_requires_uniform_images_and_low_auto_coverage():
    primary = attempt(20, 1806, 0.953)
    assert shared_retry_reason("auto", [(1600, 1067)] * 26, primary) is None
    assert (
        shared_retry_reason("auto", [(1600, 1067), (1200, 900)], primary)
        == "mixed_image_dimensions"
    )
    assert (
        shared_retry_reason("shared", [(1600, 1067)] * 26, primary)
        == "manual_camera_mode"
    )
    assert (
        shared_retry_reason("auto", [(1600, 1067)] * 26, attempt(24, 2200, 0.95))
        == "auto_coverage_sufficient"
    )


def test_shared_retry_rejects_known_mixed_35mm_focal_lengths():
    primary = attempt(20, 1806, 0.953)
    image_sizes = [(1600, 1067)] * 26
    assert (
        shared_retry_reason("auto", image_sizes, primary, [24] * 13 + [48] * 13)
        == "mixed_35mm_focal_lengths"
    )
    # Missing EXIF does not invent a lens distinction; the existing geometric
    # selection gates remain responsible for that case.
    assert shared_retry_reason("auto", image_sizes, primary, [24, None] * 13) is None
    assert (
        shared_retry_reason("auto", image_sizes, attempt(24, 2200, 0.95), [24, 48])
        == "auto_coverage_sufficient"
    )


def test_shared_retry_needs_better_coverage_points_and_bounded_error():
    primary = attempt(20, 1806, 0.953)
    assert prefer_shared_attempt(primary, attempt(26, 2497, 0.962))
    assert not prefer_shared_attempt(primary, attempt(26, 1700, 0.962))
    assert not prefer_shared_attempt(primary, attempt(20, 2497, 0.962))
    assert not prefer_shared_attempt(primary, attempt(26, 2497, 1.06))


def test_camera_model_keeps_auto_conservative_and_uses_shared_lens_capacity():
    assert camera_model_for_mode("auto") == "SIMPLE_RADIAL"
    assert camera_model_for_mode("shared") == "RADIAL"
    with pytest.raises(ValueError, match="不支持的相机模式"):
        camera_model_for_mode("per-photo")
