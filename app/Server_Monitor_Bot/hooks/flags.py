# flag(key, default): 컨테이너 SQLite 플래그 캐시를 3초 메모리 캐시로 읽는다. 꺼져 있거나 실패하면 default.
import threading
import time

from . import state

TTL = 3.0
_cache: dict[str, object] = {}
_loaded = 0.0
_lock = threading.Lock()
_db = None


def _refresh() -> None:
    global _loaded, _cache, _db
    try:
        if _db is None:
            from ..store.buffer import FlagCache
            _db = FlagCache(state.DIR / "flags.db")
        _cache = _db.all()
    except Exception:
        _cache = {}
    _loaded = time.monotonic()


def flag(key: str, default=None):
    if not state.INSTALLED:
        return default
    with _lock:
        if time.monotonic() - _loaded > TTL:
            _refresh()
        return _cache.get(key, default)
