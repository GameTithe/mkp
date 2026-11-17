"""Transactional state and append-only, hash-chained evidence records.
The hash chain detects accidental changes; the owner of this PC can rewrite it.
"""
from __future__ import annotations
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any
import uuid


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.logs = root / 'logs'
        self.logs.mkdir(parents=True, exist_ok=True)
        self.path = self.logs / 'session.sqlite3'
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, at REAL NOT NULL,
                    kind TEXT NOT NULL, channel TEXT NOT NULL, visibility TEXT NOT NULL,
                    data TEXT NOT NULL, previous_hash TEXT NOT NULL, hash TEXT NOT NULL
                );
            ''')

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=30000')
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key: str, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else default

    def set(self, key: str, value: Any):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO kv VALUES (?, ?)', (key, canonical(value)))

    def event(self, kind: str, channel: str = 'system', visibility: str = 'candidate', **data) -> str:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            last = db.execute('SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
            seq = last['seq'] + 1 if last else 1
            previous = last['hash'] if last else '0' * 64
            item = dict(seq=seq, id=f'E{seq:08d}', at=time.time(), kind=kind,
                        channel=channel, visibility=visibility, data=data, previous_hash=previous)
            digest = sha(canonical(item).encode('utf-8'))
            db.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                       (seq, item['id'], item['at'], kind, channel, visibility,
                        canonical(data), previous, digest))
        return item['id']

    def events(self, after: int = 0, public: bool = False):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM events WHERE seq > ? ORDER BY seq', (after,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['data'] = json.loads(item['data'])
            if not public or item['visibility'] == 'candidate':
                result.append(item)
        return result

    def verify(self):
        previous = '0' * 64
        for number, item in enumerate(self.events(), 1):
            digest = item.pop('hash')
            if item['seq'] != number or item['previous_hash'] != previous or sha(canonical(item).encode('utf-8')) != digest:
                return {'ok': False, 'event': item['id']}
            previous = digest
        return {'ok': True, 'events': len(self.events()), 'head': previous}

    def export(self, target: Path | None = None):
        target = target or self.logs / 'events.jsonl'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(''.join(canonical(e) + '\n' for e in self.events()), encoding='utf-8')
        return target

    @contextlib.contextmanager
    def operation(self):
        """One top-level modifying operation across CLI/server processes.
        Worker subprocesses can log concurrently. Never hold this across interactive input.
        """
        path = self.logs / 'operation.lock'
        fh = path.open('a+b')
        fh.seek(0)
        if not fh.read(1):
            fh.write(b'0')
            fh.flush()
        fh.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            fh.close()
            raise RuntimeError('다른 작업이 실행 중입니다. 해당 작업을 마친 후 실행하세요.')
        try:
            yield
        finally:
            fh.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()
