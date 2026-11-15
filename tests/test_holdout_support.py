from types import SimpleNamespace as Obj

import numpy as np
import pytest

from scripts.benchmark_holdout_support import tile_support


def make_model():
    def obs(point_id, xy=(0, 0)):
        return Obj(point3D_id=point_id, xy=np.array(xy), has_point3D=lambda: point_id is not None)

    # Non-contiguous image IDs. Both 10 and 20 are held out.
    target = Obj(name="target.jpg", camera_id=7, points2D=[
        obs(100, (1, 1)), obs(101, (16, 12)), obs(102, (33, 25)),
        obs(102, (33, 25)), obs(None), obs(103, (34, 0)),
    ])
    images = {10: target, 20: Obj(name="eval.jpg", points2D=[obs(100)]),
              90: Obj(name="a.jpg", points2D=[obs(101), obs(102)]),
              99: Obj(name="b.jpg", points2D=[obs(102), obs(103)])}
    links = {100: [(10, 0), (20, 0)], 101: [(10, 1), (90, 0)],
             102: [(10, 2), (90, 1), (99, 0), (99, 0)], 103: [(10, 5), (99, 1)]}
    points = {pid: Obj(error=.5, track=Obj(
        length=lambda items=items: len(items),
        elements=[Obj(image_id=i, point2D_idx=j) for i, j in items]))
              for pid, items in links.items()}
    return Obj(images=images, cameras={7: Obj(width=34, height=26)}, points3D=points)


def test_training_tracks_exclude_holdout_deduplicate_and_use_scoring_boundaries():
    model = make_model()
    result = tile_support(model, 10, {90, 99}, (17, 13), 2)
    tiles = result["tiles"]
    assert result["rejected_linked_observations"] == 1
    assert [(t["tracks"], t["with_training"], t["with_two_training"])
            for t in tiles] == [(1, 0, 0), (0, 0, 0), (0, 0, 0), (2, 2, 1)]
    assert tiles[-1]["xyxy"] == [8, 6, 17, 13]
    assert [t["keypoints"] for t in tiles] == [2, 0, 0, 3]
    assert tiles[-1]["occupied_subcells_of_16"] == 2
    assert tiles[0]["training_sources"] == []  # Shared only with another held-out photo.
    assert tiles[-1]["training_sources"] == [
        {"image_name": "a.jpg", "shared_tracks": 2},
        {"image_name": "b.jpg", "shared_tracks": 1},
    ]


def test_rejects_broken_track_and_training_target():
    model = make_model()
    with pytest.raises(ValueError, match="held out"):
        tile_support(model, 10, {10, 90}, (17, 13), 2)
    model.images[90].points2D[0].point3D_id = 999
    with pytest.raises(ValueError, match="reciprocal"):
        tile_support(model, 10, {90, 99}, (17, 13), 2)


def test_bad_residual_and_empty_tiles_are_not_counted_as_coverage():
    model = make_model()
    model.points3D[101].error = float("nan")
    model.points3D[102].error = 4
    result = tile_support(model, 10, {90, 99}, (17, 13), 2)
    assert result["rejected_linked_observations"] == 3
    assert sum(t["with_training"] for t in result["tiles"]) == 0
    assert all(t["occupied_subcells_of_16"] == 0 for t in result["tiles"])
