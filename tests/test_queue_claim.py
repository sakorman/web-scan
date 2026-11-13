import sqlite3
from contextlib import closing

import pytest

from webscan.store import Store


def enqueue(db, jid, created):
    db.execute(
        "INSERT INTO jobs(id,session_id,status,stage,config,created,updated) "
        "VALUES(?,'s','queued','queued','{}',?,?)",
        (jid, created, created),
    )


def test_contended_claim_preserves_queue_then_resumes_in_order(tmp_path):
    store = Store(tmp_path)
    with closing(store.connect()) as writer, writer:
        enqueue(writer, "second", 2)
        enqueue(writer, "first", 1)
        writer.commit()
        writer.execute("BEGIN IMMEDIATE")
        # Uncommitted cancellation is invisible to the poll, but it cannot claim.
        writer.execute("UPDATE jobs SET status='cancelled' WHERE id='first'")
        assert store.claim_next_job() is None
        assert store.job("second")["status"] == "queued"
    assert store.claim_next_job() == "second"
    assert store.job("first")["status"] == "cancelled"
    assert store.claim_next_job() is None


def test_claim_uses_oldest_ready_job_once(tmp_path):
    store = Store(tmp_path)
    with closing(store.connect()) as db, db:
        enqueue(db, "new", 2)
        enqueue(db, "old", 1)
    assert [store.claim_next_job() for _ in range(3)] == ["old", "new", None]


def test_claim_propagates_catalog_errors(tmp_path):
    store = Store(tmp_path)
    with closing(store.connect()) as db, db:
        db.execute("DROP TABLE jobs")
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        store.claim_next_job()


def test_cancel_between_lookup_and_claim_is_not_overwritten(tmp_path, monkeypatch):
    store = Store(tmp_path)
    with closing(store.connect()) as canceller, canceller:
        enqueue(canceller, "cancel-me", 1)
        canceller.commit()
        worker = store.connect(timeout=0)

        def cancel_before_update(statement):
            if statement.startswith("UPDATE jobs"):
                canceller.execute("UPDATE jobs SET status='cancelled' WHERE id='cancel-me'")
                canceller.commit()

        worker.set_trace_callback(cancel_before_update)
        with monkeypatch.context() as patch:
            patch.setattr(store, "connect", lambda **_: worker)
            assert store.claim_next_job() is None
        assert store.job("cancel-me")["status"] == "cancelled"
