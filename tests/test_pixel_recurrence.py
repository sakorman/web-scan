import numpy as np
import pytest

from scripts.benchmark_pixel_recurrence import high_error_mask, measure_tile, measure


def test_ties_across_decile_bound_exclude_entire_tied_group():
    error = np.array([8, 7, 7] + [0] * 17)
    assert np.flatnonzero(high_error_mask(error)).tolist() == [0]
    error[0] = 7
    assert not high_error_mask(error).any()


def test_intersection_is_fixed_before_scoring_check():
    first = np.array([9, 7, 0] + [0] * 17)
    second = np.array([8, 0, 8] + [0] * 17)
    check = np.array([2, 9, 9] + [0] * 17)
    result, frozen = measure_tile([first, second], check)
    assert np.flatnonzero(frozen).tolist() == [0]
    assert result["frozen_pixels"] == 1
    assert result["in_both"] == 0
    assert result["check_top_pixels"] == 2
    assert result["frozen_rgb_sse"] == 2
    assert result["all_rgb_sse"] == 20
    assert measure_tile([first, second], first)[0]["frozen_sha256"] == result["frozen_sha256"]


def test_check_cannot_alias_history(tmp_path):
    job = tmp_path / "jobs" / "one"
    job.mkdir(parents=True)
    (job.parent / "alias").symlink_to(job, target_is_directory=True)
    with pytest.raises(ValueError, match="distinct"):
        measure(tmp_path, ["one", "two"], "alias", {})


def test_exports_masks_on_the_verified_grid(tmp_path):
    import shutil
    from PIL import Image
    from test_comparison_size import make_jobs

    make_jobs(tmp_path, (48, 32), (48, 32))
    jobs = tmp_path / "jobs"
    shutil.copytree(jobs / "b", jobs / "c")
    regions = {"0000.png": [0], "0008.png": []}
    masks = tmp_path / "masks"
    scored = measure(tmp_path, ["a", "b"], "c", regions, masks_dir=masks)
    for row in scored["views"]:
        with Image.open(masks / row["view"]) as mask:
            assert mask.size == (48, 32)
            assert np.count_nonzero(mask) == sum(t["frozen_pixels"] for t in row["tiles"])
    with pytest.raises(ValueError, match="native render"):
        measure(tmp_path, ["a", "b"], "c", regions, size=32)
