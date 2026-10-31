import numpy as np
import pytest
import json
from types import SimpleNamespace

from scripts.benchmark_depth_consistency import camera, check_pair, grid, project, run, unproject
from scripts.benchmark_depth_prior import digest, save


CONFIG = {"relative_depth_tolerance": 0.05, "roundtrip_pixels": 2., "min_parallax_degrees": 1.}


def cal(center=(0, 0, 0), rotation=None):
    rotation = np.eye(3) if rotation is None else rotation
    return np.array([100., 100., 32.5, 32.5]), np.column_stack([rotation, -rotation @ np.array(center)])


def pair(depth=5., target=None, xy=None, z=None):
    xy = np.array([[32.5, 32.5], [42.5, 32.5]]) if xy is None else xy
    z = np.full(len(xy), 5.) if z is None else z
    return check_pair(xy, z, cal(), cal((0.5, 0, 0)) if target is None else target,
                      np.full((64, 64), depth), CONFIG)


def test_world_plane_reprojects_with_known_translation_and_nonzero_principal_point():
    xy = np.array([[32.5, 32.5], [42.5, 32.5]])
    world = unproject(xy, np.full(2, 5.), cal())
    assert np.allclose(world, [[0, 0, 5], [0.5, 0, 5]])
    projected, z = project(world, cal((0.5, 0, 0)))
    assert np.allclose(projected, [[22.5, 32.5], [32.5, 32.5]])
    assert np.all(z == 5)
    assert pair()["supported"].all()


def test_occlusion_candidate_and_front_conflict_keep_opposite_depth_signs():
    assert pair(depth=4)["occlusion_candidate"].all()
    assert pair(depth=6)["front_depth_conflict"].all()
    assert not pair(depth=4)["supported"].any()


def test_pure_rotation_and_duplicate_pose_cannot_supply_parallax_support():
    r = np.array([[0., -1, 0], [1, 0, 0], [0, 0, 1]])
    for target in (cal(), cal(rotation=r)):
        assert pair(target=target)["low_parallax"].all()
        assert not pair(target=target)["supported"].any()


def test_roundtrip_rejects_small_relative_depth_error_at_large_baseline():
    # 4% depth agreement passes, but a large translation makes >2px roundtrip error.
    masks = pair(depth=5.2, target=cal((3, 0, 0)), xy=np.array([[62.5, 32.5]]))
    assert masks["roundtrip_failed"].all()


def test_behind_camera_outside_and_invalid_depth_are_unavailable_not_supported():
    assert pair(target=cal((0, 0, 10)))["unavailable_target"].all()
    assert pair(target=cal((100, 0, 0)))["unavailable_target"].all()
    assert pair(depth=0)["unavailable_target"].all()
    assert pair(z=np.array([np.nan, -1.]))["invalid_source"].all()


def test_scaled_scene_and_camera_translations_keep_the_same_decision():
    xy = np.array([[32.5, 32.5]])
    for scale in (0.01, 1., 100.):
        masks = check_pair(xy, np.array([5.*scale]), cal(), cal((0.5*scale, 0, 0)),
                           np.full((64, 64), 5.*scale), CONFIG)
        assert masks["supported"].all()


def test_grid_uses_native_pixel_centers_and_calibration_rejects_distortion():
    xy, shape = grid(1600, 1066, 8)
    assert shape == (133, 200)
    assert xy[0].tolist() == [4.5, 4.5]
    assert xy[-1].tolist() == [1596.5, 1060.5]
    view = {"camera_model": "RADIAL", "camera_params": [1, 2, 3], "cam_from_world": []}
    with pytest.raises(ValueError, match="PINHOLE"):
        camera(view)
    view.update(camera_model="PINHOLE", camera_params=cal()[0].tolist(), cam_from_world=cal()[1].tolist())
    assert np.allclose(camera(view)[1], cal()[1])
    view["cam_from_world"][0][0] = 2
    with pytest.raises(ValueError, match="rigid pose"):
        camera(view)


def make_bundle(tmp_path, passing):
    views, rows, predictions = [], [], []
    for i, passed in enumerate(passing):
        image = tmp_path / f"{i}.jpg"
        image.write_bytes(f"distinct-image-{i}".encode())  # Only image identity is read by this stage.
        path = tmp_path / f"{i}.npy"
        np.save(path, np.ones((64, 64)))
        intrinsics, pose = cal((0.5*i, 0, 0))
        views.append({"job": "test", "name": image.name, "image": str(image), "image_sha256": digest(image),
                      "width": 64, "height": 64, "camera_model": "PINHOLE", "camera_params": intrinsics.tolist(),
                      "cam_from_world": pose.tolist()})
        rows.append({"job": "test", "name": image.name, "passed": passed, "scale": .2, "shift": 0,
                     "reason": "screen_pass" if passed else "screen_threshold_failed"})
        predictions.append({"job": "test", "name": image.name, "prediction": path.name,
                            "prediction_sha256": digest(path), "image_sha256": digest(image)})
    args = SimpleNamespace(anchors=tmp_path / "anchors.json", analysis=tmp_path / "analysis.json",
                           predictions=tmp_path, config=tmp_path / "config.json", output=tmp_path / "result")
    save(args.anchors, {"views": views})
    save(args.analysis, {"views": rows, "anchor_sha256": digest(args.anchors),
                         "inference": {"status": "complete", "anchor_sha256": digest(args.anchors), "views": predictions}})
    save(args.config, {"cross_view": {**CONFIG, "grid_step": 8, "min_other_views": 2}})
    return args


@pytest.mark.parametrize("passing,expected", [([True, True], 0), ([True, True, True], 48), ([True, True, False], 0)])
def test_two_additional_views_are_required_and_failed_views_cannot_vote(tmp_path, passing, expected):
    args = make_bundle(tmp_path, passing)
    run(args)
    result = json.loads((args.output / "consistency.json").read_text())
    scene = result["scenes"][0]
    assert scene["views"][0]["retained_samples"] == expected
    assert len(scene["pairs"]) == sum(passing)*(sum(passing)-1)
    assert len(scene["excluded_views"]) == len(passing)-sum(passing)


def test_modified_prediction_is_rejected_and_failure_is_preserved(tmp_path):
    args = make_bundle(tmp_path, [True, True, True])
    np.save(tmp_path / "0.npy", np.full((64, 64), 2.))
    with pytest.raises(AssertionError):
        run(args)
    assert json.loads((args.output / "consistency.json").read_text())["status"] == "failed"
