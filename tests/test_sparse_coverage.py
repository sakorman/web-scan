import numpy as np

from webscan.reconstruction import (
    camera_direction_coverage,
    camera_spacing_coverage,
    sparse_view_coverage,
)


class Point:
    def __init__(self, point_id):
        self.point3D_id = point_id

    def has_point3D(self):
        return self.point3D_id is not None


class Image:
    def __init__(self, name, point_ids):
        self.name = name
        self.points2D = [Point(point_id) for point_id in point_ids]


class Reconstruction:
    def __init__(self):
        self.images = {
            3: Image("0002.jpg", list(range(1, 21))),
            1: Image("0000.jpg", list(range(1, 121))),
            2: Image("0001.jpg", list(range(1, 71)) + [None]),
        }


def test_sparse_coverage_marks_only_relatively_weak_registered_views():
    coverage = sparse_view_coverage(Reconstruction(), set(range(1, 121)))

    assert coverage == {
        "method": "valid_sparse_track_observations",
        "valid_sparse_points": 120,
        "registered_views": 3,
        "median_observations": 70.0,
        "support_floor": 64,
        "weak_views": [{"image_name": "0002.jpg", "observations": 20}],
    }


def test_sparse_coverage_handles_empty_reconstructions():
    assert sparse_view_coverage(type("Empty", (), {"images": {}})(), set()) is None


def view(name, center):
    matrix = np.eye(4)
    matrix[:3, 3] = -np.asarray(center)
    return {"image_name": name, "view": matrix.tolist()}


def test_camera_spacing_marks_only_relatively_isolated_registered_views():
    coverage = camera_spacing_coverage(
        [
            view("0002.jpg", [0.2, 0, 0]),
            view("0003.jpg", [1.0, 0, 0]),
            view("0000.jpg", [0, 0, 0]),
            view("0001.jpg", [0.1, 0, 0]),
        ]
    )

    assert coverage == {
        "method": "nearest_registered_camera_spacing",
        "units": "scene_normalized",
        "registered_views": 4,
        "median_nearest_distance": 0.1,
        "spacing_floor": 0.25,
        "isolated_views": [
            {"image_name": "0003.jpg", "nearest_distance": 0.8}
        ],
    }


def test_camera_spacing_handles_coincident_or_too_few_camera_centers():
    assert camera_spacing_coverage([view("0000.jpg", [0, 0, 0])]) is None
    assert camera_spacing_coverage(
        [
            view("0000.jpg", [0, 0, 0]),
            view("0001.jpg", [0, 0, 0]),
            view("0002.jpg", [0, 0, 0]),
        ]
    ) == {
        "method": "nearest_registered_camera_spacing",
        "units": "scene_normalized",
        "registered_views": 3,
        "median_nearest_distance": 0.0,
        "spacing_floor": None,
        "isolated_views": [],
        "reason": "coincident_camera_centers",
    }


def direction_view(name, forward):
    matrix = np.eye(4)
    matrix[2, :3] = np.asarray(forward, dtype=float)
    return {"image_name": name, "view": matrix.tolist()}


def test_camera_direction_marks_narrow_parallel_headings_without_using_position():
    coverage = camera_direction_coverage(
        [
            direction_view("0002.jpg", [0, 0, 1]),
            direction_view("0000.jpg", [0, 0, 1]),
            direction_view("0001.jpg", [0, 0, 1]),
        ]
    )

    assert coverage == {
        "method": "forward_direction_pairwise_angles",
        "units": "degrees",
        "registered_views": 3,
        "median_pairwise_angle_degrees": 0.0,
        "p90_pairwise_angle_degrees": 0.0,
        "max_pairwise_angle_degrees": 0.0,
        "narrow_coverage": True,
        "narrow_p90_threshold_degrees": 20.0,
    }


def test_camera_direction_reports_wide_headings_and_too_few_views():
    assert camera_direction_coverage([direction_view("0000.jpg", [0, 0, 1])]) is None
    coverage = camera_direction_coverage(
        [
            direction_view("0000.jpg", [1, 0, 0]),
            direction_view("0001.jpg", [0, 1, 0]),
            direction_view("0002.jpg", [0, 0, 1]),
        ]
    )

    assert coverage == {
        "method": "forward_direction_pairwise_angles",
        "units": "degrees",
        "registered_views": 3,
        "median_pairwise_angle_degrees": 90.0,
        "p90_pairwise_angle_degrees": 90.0,
        "max_pairwise_angle_degrees": 90.0,
        "narrow_coverage": False,
        "narrow_p90_threshold_degrees": 20.0,
    }
