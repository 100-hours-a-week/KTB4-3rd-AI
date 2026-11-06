# repos 공용: 시각 변환, id 생성, JSON 열 처리.
import json
import secrets
import string
import time
from datetime import datetime, timezone

ALPHABET = string.ascii_lowercase + string.digits


def dt(ts: float | None = None) -> datetime:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).replace(tzinfo=None)


def epoch(value: datetime | None) -> float | None:
    return None if value is None else value.replace(tzinfo=timezone.utc).timestamp()


def new_id(prefix: str) -> str:
    return f"{prefix}-{dt():%Y%m%d}-{''.join(secrets.choice(ALPHABET) for _ in range(4))}"


def js(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def unjs(value):
    if value is None or isinstance(value, (list, dict)):
        return value
    return json.loads(value)


def insert_with_id(db, prefix: str, sql: str, params: tuple, tries: int = 3) -> str:
    import pymysql

    for attempt in range(tries):
        rid = new_id(prefix)
        try:
            db.execute(sql, (rid, *params))
            return rid
        except pymysql.err.IntegrityError:
            if attempt == tries - 1:
                raise
    raise RuntimeError("unreachable")
