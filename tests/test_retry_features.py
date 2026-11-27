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
        db.execute("CREATE TABLE matches(pair_id INTEGER PRIMARY KEY, rows, cols, data)")
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


@pytest.mark.parametrize("target_ids, expected_pairs", [((3, 4), 1), ((4, 3), 0)])
def test_retry_only_copies_raw_pairs_for_identical_ids(tmp_path, target_ids, expected_pairs):
    source = tmp_path / "source.db"
    target = tmp_path / "retry.db"
    make_database(source, [(3, "a.jpg"), (4, "b.jpg")])
    make_database(target, list(zip(target_ids, ["a.jpg", "b.jpg"])))
    pair_id = 3 * 2147483647 + 4
    with closing(sqlite3.connect(source)) as db, db:
        for table in ("keypoints", "descriptors"):
            db.executemany(f"INSERT INTO {table} VALUES (?,1,2,?)", [(3, b"aa"), (4, b"bb")])
        db.execute("INSERT INTO matches VALUES (?,1,2,?)", (pair_id, bytes(8)))
        db.execute("INSERT INTO two_view_geometries VALUES (?)", (pair_id,))
    assert _reuse_retry_features(source, target, 2)
    with closing(sqlite3.connect(target)) as db:
        assert db.execute("SELECT * FROM matches").fetchall() == [(pair_id, 1, 2, bytes(8))] * expected_pairs
        assert db.execute("SELECT COUNT(*) FROM two_view_geometries").fetchone() == (0,)
