import sqlite3

import pytest

from webscan.store import Store


def assert_closed(db):
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        _ = db.in_transaction


def test_connection_commits_and_closes_even_when_reference_is_retained(tmp_path):
    store = Store(tmp_path)
    with store.transaction() as db:
        db.execute("INSERT INTO sessions VALUES('saved','name','capturing','photos',0)")
    assert_closed(db)
    assert store.session("saved")["name"] == "name"


def test_connection_rolls_back_and_closes_on_body_error(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(ValueError, match="abort"):
        with store.transaction() as db:
            db.execute("INSERT INTO sessions VALUES('aborted','name','capturing','photos',0)")
            raise ValueError("abort")
    assert_closed(db)
    with pytest.raises(KeyError):
        store.session("aborted")


def test_connection_closes_when_deferred_constraint_fails_at_commit(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
            db.execute("CREATE TABLE child(parent_id INTEGER REFERENCES parent(id) "
                       "DEFERRABLE INITIALLY DEFERRED)")
            db.execute("INSERT INTO child VALUES(42)")
            assert db.in_transaction
    assert_closed(db)
    with store.transaction() as reader:
        assert reader.execute("SELECT COUNT(*) FROM child").fetchone()[0] == 0


def test_store_and_api_release_all_connections(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from webscan.api import create_app

    opened = []
    original = Store.connect

    def track(self, **kwargs):
        db = original(self, **kwargs)
        opened.append(db)
        return db

    monkeypatch.setattr(Store, "connect", track)
    app = create_app(tmp_path, run_worker=False)
    with TestClient(app) as client:
        session = client.post("/api/sessions", json={"name": "saved"})
        assert session.status_code == 201
        sid = session.json()["id"]
        assert client.get("/api/sessions").status_code == 200
        assert client.get(f"/api/sessions/{sid}").status_code == 200
        assert client.get("/api/jobs/" + "0" * 32).status_code == 404
        assert client.delete(f"/api/sessions/{sid}").status_code == 200
        assert client.post(f"/api/sessions/{sid}/restore").status_code == 200
    assert opened
    for db in opened:
        assert_closed(db)
