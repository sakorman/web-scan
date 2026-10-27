from webscan.reconstruction import sparse_view_coverage


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
