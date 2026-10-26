from webscan.reconstruction import prefer_shared_attempt, shared_retry_reason


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


def test_shared_retry_needs_better_coverage_points_and_bounded_error():
    primary = attempt(20, 1806, 0.953)
    assert prefer_shared_attempt(primary, attempt(26, 2497, 0.962))
    assert not prefer_shared_attempt(primary, attempt(26, 1700, 0.962))
    assert not prefer_shared_attempt(primary, attempt(20, 2497, 0.962))
    assert not prefer_shared_attempt(primary, attempt(26, 2497, 1.06))
