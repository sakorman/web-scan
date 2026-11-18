"""Count open SQLite file descriptors on macOS; optional baseline Git revision."""
import gc
import json
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from webscan.store import Store


def handles(root):
    result = subprocess.run(['lsof', '-a', '-p', str(os.getpid()), '-Ffn'], capture_output=True, text=True, check=True)
    prefix = 'n' + str(root / 'state.sqlite3')
    count = 0
    descriptor = ''
    for line in result.stdout.splitlines():
        if line.startswith('f'):
            descriptor = line[1:]
        elif line.startswith(prefix) and descriptor.isdigit():
            count += 1
    return count


if len(sys.argv) > 2:
    import types
    module = types.ModuleType('webscan._baseline_store')
    module.__package__ = 'webscan'
    source = subprocess.run(['git', 'show', sys.argv[2] + ':webscan/store.py'], check=True, capture_output=True, text=True).stdout
    exec(compile(source, '<baseline-store>', 'exec'), module.__dict__)
    Store = module.Store

result = {'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version, 'gc_threshold': gc.get_threshold(), 'samples': []}
with tempfile.TemporaryDirectory(dir=sys.argv[1]) as temp:
    root = Path(temp).resolve()
    store = Store(root)
    sid = store.create_session('connection-probe')['id']
    gc.collect()
    for deferred in [False, True]:
        gc.collect()
        if deferred:
            gc.disable()
        counts = [{'reads': 0, 'database_handles': handles(root)}]
        try:
            for index in range(1, 201):
                assert store.session(sid)['name'] == 'connection-probe'
                if index in (25, 50, 100, 200):
                    counts.append({'reads': index, 'database_handles': handles(root)})
        finally:
            gc.enable()
        gc.collect()
        result['samples'].append({'gc_deferred': deferred, 'counts': counts, 'after_collection': handles(root)})
    with getattr(store, "transaction", store.connect)() as db:
        db.execute('SELECT 1').fetchone()
    try:
        db.execute('SELECT 1').fetchone()
        result['closed_at_scope_exit'] = False
    except sqlite3.ProgrammingError:
        result['closed_at_scope_exit'] = True
    finally:
        db.close()
print(json.dumps(result, indent=2))
