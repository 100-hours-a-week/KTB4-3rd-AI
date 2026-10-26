# 컨테이너 SQLite: 워커 → 분석 프로세스 기록 버퍼(buffer.db)와 플래그 캐시(flags.db). 표준 라이브러리만 쓴다.
import json
import sqlite3
import threading
import time
from pathlib import Path


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5, check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


class Outbox:
    def __init__(self, path: Path, max_mb: int = 50):
        self.path = path
        self.max_bytes = max_mb * 1024 * 1024
        self.conn = connect(path)
        self.lock = threading.Lock()
        self.conn.execute("CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, payload TEXT NOT NULL, ts REAL NOT NULL)")

    def put_many(self, rows: list[tuple[str, dict]]) -> None:
        if not rows:
            return
        now = time.time()
        with self.lock:
            self.conn.executemany("INSERT INTO outbox (kind, payload, ts) VALUES (?, ?, ?)",
                                  [(kind, json.dumps(payload, ensure_ascii=False, default=str), now) for kind, payload in rows])
            self.trim()

    def trim(self) -> None:
        size = self.path.stat().st_size if self.path.exists() else 0
        if size > self.max_bytes:
            self.conn.execute("DELETE FROM outbox WHERE id IN (SELECT id FROM outbox ORDER BY id LIMIT 1000)")

    def read(self, limit: int = 500) -> list[tuple[int, str, dict]]:
        with self.lock:
            rows = self.conn.execute("SELECT id, kind, payload FROM outbox ORDER BY id LIMIT ?", (limit,)).fetchall()
        return [(i, kind, json.loads(payload)) for i, kind, payload in rows]

    def delete_upto(self, last_id: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM outbox WHERE id <= ?", (last_id,))

    def count(self) -> int:
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]


class FlagCache:
    def __init__(self, path: Path):
        self.conn = connect(path)
        self.lock = threading.Lock()
        self.conn.execute("CREATE TABLE IF NOT EXISTS flags (key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at REAL)")

    def replace(self, flags: dict[str, tuple[object, float | None]]) -> None:
        with self.lock:
            self.conn.execute("BEGIN")
            self.conn.execute("DELETE FROM flags")
            self.conn.executemany("INSERT INTO flags (key, value, expires_at) VALUES (?, ?, ?)",
                                  [(k, json.dumps(v), exp) for k, (v, exp) in flags.items()])
            self.conn.execute("COMMIT")

    def all(self) -> dict[str, object]:
        now = time.time()
        with self.lock:
            rows = self.conn.execute("SELECT key, value, expires_at FROM flags").fetchall()
        return {k: json.loads(v) for k, v, exp in rows if exp is None or exp > now}
