from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path


def uid() -> str:
    return uuid.uuid4().hex


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                  id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'capturing',
                  kind TEXT NOT NULL DEFAULT 'photos', created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS frames (
                  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, sha TEXT NOT NULL,
                  width INTEGER NOT NULL, height INTEGER NOT NULL, bytes INTEGER NOT NULL,
                  metadata TEXT NOT NULL, UNIQUE(session_id, sha));
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, status TEXT NOT NULL,
                  stage TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
                  message TEXT NOT NULL DEFAULT '', config TEXT NOT NULL,
                  report TEXT, created REAL NOT NULL, updated REAL NOT NULL);
            """)

    def connect(self):
        db = sqlite3.connect(self.root / "state.sqlite3", timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def session_dir(self, sid):
        return self.root / "sessions" / sid

    def job_dir(self, jid):
        return self.root / "jobs" / jid

    def session(self, sid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            if not row or row["status"] == "deleted":
                raise KeyError("采集不存在")
            result = dict(row)
            result["frames"] = [
                dict(x)
                for x in db.execute(
                    "SELECT * FROM frames WHERE session_id=? ORDER BY rowid", (sid,)
                )
            ]
            for frame in result["frames"]:
                frame["metadata"] = json.loads(frame["metadata"])
            result["jobs"] = [
                self.decode_job(x)
                for x in db.execute(
                    "SELECT * FROM jobs WHERE session_id=? ORDER BY created DESC",
                    (sid,),
                )
            ]
            return result

    def create_session(self, name, kind="photos"):
        sid = uid()
        (self.session_dir(sid) / "images").mkdir(parents=True)
        with self.connect() as db:
            db.execute(
                "INSERT INTO sessions(id,name,kind,created) VALUES(?,?,?,?)",
                (sid, name, kind, time.time()),
            )
        return self.session(sid)

    @staticmethod
    def decode_job(row):
        value = dict(row)
        value["config"] = json.loads(value["config"])
        value["report"] = json.loads(value["report"]) if value["report"] else None
        return value

    def job(self, jid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
            if not row:
                raise KeyError("任务不存在")
            return self.decode_job(row)

    def update(self, jid, *, stage, progress, message, status=None, report=None):
        with self.connect() as db:
            db.execute(
                """UPDATE jobs SET stage=?, progress=?, message=?, updated=?,
                         status=COALESCE(?,status), report=COALESCE(?,report)
                         WHERE id=? AND status IN ('queued','running')""",
                (
                    stage,
                    progress,
                    message,
                    time.time(),
                    status,
                    json.dumps(report, ensure_ascii=False)
                    if report is not None
                    else None,
                    jid,
                ),
            )
