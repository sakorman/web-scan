import asyncio
import contextlib
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from webscan.api import create_app


def test_health_reads_existing_catalog_without_changing_history(tmp_path):
    app = create_app(tmp_path / "catalog #? 中文", run_worker=False)
    sid = app.state.store.create_session("saved")["id"]
    with TestClient(app) as client:
        before = client.get("/api/sessions").json()
        for _ in range(3):
            response = client.get("/api/health")
            assert response.status_code == 200
            assert response.json()["database_available"] is True
            assert response.json()["worker_running"] is None
        assert client.get("/api/sessions").json() == before
        assert before[0]["id"] == sid


def test_health_missing_database_does_not_create_replacement_and_recovers(tmp_path):
    root = tmp_path / "data"
    app = create_app(root, run_worker=False)
    saved = tmp_path / "saved"
    with TestClient(app) as client:
        root.rename(saved)
        try:
            response = client.get("/api/health")
            assert response.status_code == 503
            assert response.json()["status"] == "degraded"
            assert response.json()["database_available"] is False
            assert not root.exists()
            assert str(tmp_path) not in response.text
        finally:
            saved.rename(root)
        assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize("table", ["sessions", "frames", "jobs"])
def test_health_checks_each_required_table(tmp_path, table):
    app = create_app(tmp_path, run_worker=False)
    with TestClient(app) as client:
        with app.state.store.connect() as db:
            db.execute(f"ALTER TABLE {table} RENAME TO saved")
        assert client.get("/api/health").status_code == 503
        with app.state.store.connect() as db:
            db.execute(f"ALTER TABLE saved RENAME TO {table}")
        assert client.get("/api/health").status_code == 200


def test_health_can_read_while_wal_writer_holds_transaction(tmp_path):
    app = create_app(tmp_path, run_worker=False)
    with TestClient(app) as client, contextlib.closing(app.state.store.connect()) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO sessions VALUES('uncommitted','pending','capturing','photos',0)")
        try:
            assert client.get("/api/health").status_code == 200
            assert client.get("/api/sessions").json() == []
        finally:
            writer.rollback()


def test_health_lock_wait_is_bounded_and_recovers(tmp_path):
    app = create_app(tmp_path, run_worker=False)
    with TestClient(app) as client, contextlib.closing(app.state.store.connect()) as writer:
        writer.execute("PRAGMA journal_mode=DELETE")
        writer.execute("BEGIN EXCLUSIVE")
        try:
            started = time.monotonic()
            assert client.get("/api/health").status_code == 503
            assert time.monotonic() - started < 3
        finally:
            writer.rollback()
        assert client.get("/api/health").status_code == 200


def test_health_does_not_report_stopped_worker_as_ready(tmp_path):
    app = create_app(tmp_path, run_worker=True)
    with TestClient(app) as client:
        assert client.get("/api/health").json()["worker_running"] is True

        async def stop():
            task = app.state.worker_task
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        client.portal.call(stop)
        response = client.get("/api/health")
        assert response.status_code == 503
        assert response.json()["worker_running"] is False
        assert response.json()["database_available"] is True


def test_health_missing_file_does_not_create_an_empty_database(tmp_path):
    app = create_app(tmp_path, run_worker=False)
    path = tmp_path / "state.sqlite3"
    saved = tmp_path / "saved.sqlite3"
    with TestClient(app) as client:
        path.rename(saved)
        try:
            assert client.get("/api/health").status_code == 503
            assert not path.exists()
        finally:
            saved.rename(path)
        assert client.get("/api/health").status_code == 200


def test_health_rejects_non_database_file(tmp_path):
    app = create_app(tmp_path / "valid", run_worker=False)
    broken = tmp_path / "invalid"
    broken.mkdir()
    raw = b"not a SQLite database" * 512
    (broken / "state.sqlite3").write_bytes(raw)
    app.state.store.root = broken
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 503
        assert response.json()["database_available"] is False
        assert (broken / "state.sqlite3").read_bytes() == raw
