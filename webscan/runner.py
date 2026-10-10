from __future__ import annotations

import asyncio
import fcntl
import os
import signal
import subprocess
import sys
import time


class Runner:
    """One local worker, with an exclusive process lock and durable SQLite queue."""

    def __init__(self, store):
        self.store = store
        self.process = None
        self.jid = None
        self.log = None
        self.lock = (store.root / "worker.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError(
                "此数据目录已有服务在运行，请关闭旧进程或指定其他 --data。"
            )
        with store.connect() as db:
            db.execute(
                "UPDATE jobs SET status='failed',stage='interrupted',message='服务被中断；可以重新提交重建',updated=? WHERE status='running'",
                (time.time(),),
            )

    async def loop(self):
        try:
            while True:
                if self.process:
                    if (
                        self.store.job(self.jid)["status"] == "cancelled"
                        and self.process.poll() is None
                    ):
                        await self.stop_process()
                    code = self.process.poll()
                    if code is not None:
                        if self.store.job(self.jid)["status"] == "running":
                            self.store.update(
                                self.jid,
                                stage="failed",
                                progress=0,
                                message=f"重建进程退出（{code}），请查看任务日志。",
                                status="failed",
                            )
                        self.log.close()
                        self.process = None
                        self.log = None
                if not self.process:
                    with self.store.connect() as db:
                        db.execute("BEGIN IMMEDIATE")
                        row = db.execute(
                            "SELECT id FROM jobs WHERE status='queued' ORDER BY created LIMIT 1"
                        ).fetchone()
                        if row:
                            self.jid = row["id"]
                            db.execute(
                                "UPDATE jobs SET status='running',updated=? WHERE id=?",
                                (time.time(), self.jid),
                            )
                    if row:
                        try:
                            path = self.store.job_dir(self.jid)
                            path.mkdir(parents=True, exist_ok=True)
                            self.log = (path / "worker.log").open("ab")
                            env = {
                                **os.environ,
                                "PYTHONUNBUFFERED": "1",
                                "OMP_NUM_THREADS": "4",
                                "OPENBLAS_NUM_THREADS": "4",
                            }
                            self.process = subprocess.Popen(
                                [
                                    sys.executable,
                                    "-m",
                                    "webscan.worker",
                                    "--data",
                                    str(self.store.root),
                                    "--job",
                                    self.jid,
                                ],
                                stdout=self.log,
                                stderr=subprocess.STDOUT,
                                env=env,
                                start_new_session=True,
                            )
                        except Exception as e:
                            self.store.update(
                                self.jid,
                                stage="failed",
                                progress=0,
                                message=str(e),
                                status="failed",
                            )
                            if self.log:
                                self.log.close()
                                self.log = None
                await asyncio.sleep(0.5)
        finally:
            if self.process and self.process.poll() is None:
                await self.stop_process()
                self.store.update(
                    self.jid,
                    stage="interrupted",
                    progress=0,
                    message="服务已停止，请重新提交任务。",
                    status="failed",
                )
            if self.log:
                self.log.close()
            self.lock.close()

    async def stop_process(self):
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        for _ in range(30):
            if self.process.poll() is not None:
                return
            await asyncio.sleep(0.1)
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await asyncio.to_thread(self.process.wait)
