"""Measure an empty worker poll under a separate WAL writer; no production database."""

import asyncio
import json
import sqlite3
import sys
import tempfile
import threading
import time
from contextlib import closing, suppress
from pathlib import Path

from webscan.runner import Runner
from webscan.store import Store


async def probe(directory, seconds):
    store = Store(directory)
    runner = Runner(store)
    locked = threading.Event()

    def writer():
        with closing(store.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            locked.set()
            time.sleep(seconds)
            db.rollback()

    thread = threading.Thread(target=writer)
    thread.start()
    assert locked.wait(timeout=5)
    start = time.perf_counter()
    task = asyncio.create_task(runner.loop())
    await asyncio.sleep(0.05)
    first = time.perf_counter() - start
    await asyncio.to_thread(thread.join)
    await asyncio.sleep(0.55)
    alive = not task.done()
    task.cancel()
    error = None
    try:
        with suppress(asyncio.CancelledError):
            await task
    except sqlite3.OperationalError as exc:
        error = exc.sqlite_errorname
    return {
        "writer_hold_seconds": seconds,
        "first_50ms_timer_seconds": round(first, 6),
        "worker_alive_after_release": alive,
        "sqlite_error": error,
    }


async def main():
    with tempfile.TemporaryDirectory(dir=sys.argv[1]) as temp:
        rows = [await probe(Path(temp) / str(seconds), seconds) for seconds in (1.2, 31)]
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
