from contextlib import closing
import sqlite3

import pytest

from webscan.reconstruction import _reuse_retry_features


def make_database(path, image_rows):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE images(image_id INTEGER PRIMARY KEY, name TEXT UNIQUE)")
        db.executemany("INSERT INTO images VALUES (?, ?)", image_rows)
        for table in ("keypoints", "descriptors"):
            db.execute(f"CREATE TABLE {table}(image_id INTEGER PRIMARY KEY, rows, cols, data)")
        db.execute("CREATE TABLE two_view_geometries(pair_id INTEGER PRIMARY KEY)")


@pytest.mark.parametrize("missing_descriptor", [False, True])
def test_same_job_feature_transfer_maps_by_name_and_rolls_back(tmp_path, missing_descriptor):
    source, target = tmp_path / "one.db", tmp_path / "two.db"
    make_database(source, [(3, "a.jpg"), (4, "b.jpg")])
    make_database(target, [(9, "b.jpg"), (10, "a.jpg")])
    with closing(sqlite3.connect(source)) as db, db:
        db.executemany("INSERT INTO keypoints VALUES (?,1,2,?)", [(3,b"first"),(4,b"second")])
        db.execute("INSERT INTO descriptors VALUES (3,1,128,?)", (b"alpha",))
        if not missing_descriptor:
            db.execute("INSERT INTO descriptors VALUES (4,1,128,?)", (b"beta",))
        db.execute("INSERT INTO two_view_geometries VALUES (123)")
    assert _reuse_retry_features(source, target, 2) == (not missing_descriptor)
    with closing(sqlite3.connect(target)) as db:
        expected = [] if missing_descriptor else [(9,b"second"),(10,b"first")]
        assert db.execute("SELECT image_id,data FROM keypoints ORDER BY image_id").fetchall() == expected
        expected = [] if missing_descriptor else [(9,b"beta"),(10,b"alpha")]
        assert db.execute("SELECT image_id,data FROM descriptors ORDER BY image_id").fetchall() == expected
        assert db.execute("SELECT count(*) FROM two_view_geometries").fetchone() == (0,)
