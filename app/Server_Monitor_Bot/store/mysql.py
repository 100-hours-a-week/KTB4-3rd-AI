# MySQL 얇은 래퍼(PyMySQL + DBUtils PooledDB). 분석 프로세스 안에서만 만든다. 값은 항상 플레이스홀더로 넘긴다.
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Sequence
from urllib.parse import unquote, urlparse

import pymysql
from dbutils.pooled_db import PooledDB
from pymysql.cursors import DictCursor

Params = Sequence[Any] | Mapping[str, Any] | None


def parse_url(url: str) -> dict:
    u = urlparse(url)
    return {"host": u.hostname or "127.0.0.1", "port": u.port or 3306, "user": unquote(u.username or ""),
            "password": unquote(u.password or ""), "database": u.path.lstrip("/")}


class MySQLDB:
    def __init__(self, *, host: str, port: int, user: str, password: str, database: str, max_connections: int = 2):
        self._conn_kwargs = dict(host=host, port=port, user=user, password=password, database=database, charset="utf8mb4",
                                 cursorclass=DictCursor, connect_timeout=5, read_timeout=10, write_timeout=10,
                                 init_command="SET time_zone = '+00:00'")
        self._pool = PooledDB(creator=pymysql, mincached=0, maxconnections=max_connections, blocking=True, ping=1,
                              autocommit=True, **self._conn_kwargs)

    @classmethod
    def from_url(cls, url: str, max_connections: int = 2) -> "MySQLDB":
        return cls(**parse_url(url), max_connections=max_connections)

    def ping(self) -> bool:
        try:
            return self.fetch_one("SELECT 1 AS ok") is not None
        except pymysql.MySQLError:
            return False

    def execute(self, sql: str, params: Params = None) -> int:
        conn = self._pool.connection()
        try:
            with conn.cursor() as cur:
                return cur.execute(sql, params)
        finally:
            conn.close()

    def insert(self, sql: str, params: Params = None) -> int:
        conn = self._pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.lastrowid
        finally:
            conn.close()

    def insert_many(self, sql: str, rows: Sequence[Params]) -> int:
        if not rows:
            return 0
        conn = self._pool.connection()
        try:
            with conn.cursor() as cur:
                return cur.executemany(sql, rows)
        finally:
            conn.close()

    def fetch_one(self, sql: str, params: Params = None) -> dict | None:
        conn = self._pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()
        finally:
            conn.close()

    def fetch_all(self, sql: str, params: Params = None) -> list[dict]:
        conn = self._pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        conn = self._pool.connection()
        try:
            conn.begin()
            with conn.cursor() as cur:
                yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def lock_connection(self):
        return pymysql.connect(autocommit=True, **self._conn_kwargs)
