"""Atomic JSON checkpoints, source fingerprints and process-safe model locks."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile

CORE_FILES = ('report.json', 'plan.json', 'bom.json', 'source_map.json', 'model_steps.mpd')

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write('\n')
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)

def save_marker(folder, name, files, key):
    write_json(Path(folder) / name, {'fingerprint': key, 'files': {n: digest(Path(folder) / n) for n in files}})

def marker_valid(folder, name, files, key):
    try:
        row = read_json(Path(folder) / name)
        return (row.get('fingerprint') == key and set(row.get('files', {})) == set(files)
                and all(digest(Path(folder) / n) == h for n, h in row['files'].items()))
    except (OSError, ValueError, KeyError):
        return False

@contextmanager
def model_lock(root, source_hash):
    path = Path(root) / '.pipeline_locks' / (source_hash + '.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as f:
        f.seek(0, 2)
        if not f.tell():
            f.write(b'0'); f.flush()
        f.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError('同一模型已有任务在运行；请等待原任务结束，不要重复启动。') from error
        try:
            yield
        finally:
            f.seek(0)
            if os.name == 'nt':
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
