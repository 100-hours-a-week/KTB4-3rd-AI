# install(app): 조건 확인 → app 로거에 LogTap 추가 → 조립 쓰레드 시작. 실패하면 경고 한 줄만 남기고 서비스는 그대로.
import importlib.util
import logging
import os
from pathlib import Path

from . import flags, state
from .assemble import Assembler
from .tap import LogTap

RUNTIME_DEPS = ("langgraph", "langchain_openai", "pymysql", "dbutils", "qdrant_client")
log = logging.getLogger("app.Server_Monitor_Bot")
_assembler: Assembler | None = None


def install(stage: str, buffer_dir: Path, max_mb: int = 50) -> bool:
    global _assembler
    if state.INSTALLED:
        return True
    missing = [m for m in RUNTIME_DEPS if importlib.util.find_spec(m) is None]
    if missing:
        log.warning("monitor disabled: reason=missing_deps missing=%s", ",".join(missing))
        return False
    try:
        buffer_dir.mkdir(parents=True, exist_ok=True)
        if not os.access(buffer_dir, os.W_OK):
            raise PermissionError(str(buffer_dir))
        from ..store.buffer import Outbox

        outbox = Outbox(buffer_dir / "buffer.db", max_mb)
    except Exception as exc:
        log.warning("monitor disabled: reason=buffer error=%s", type(exc).__name__)
        return False
    state.STAGE, state.DIR = stage, buffer_dir
    flags._db, flags._loaded = None, 0.0
    logging.getLogger("app").addHandler(LogTap(level=logging.INFO))
    _assembler = Assembler(outbox)
    _assembler.start()
    state.INSTALLED = True
    return True


def uninstall() -> None:
    global _assembler
    if _assembler:
        _assembler.stop_event.set()
        _assembler.join(timeout=3)
        _assembler = None
    app_logger = logging.getLogger("app")
    for h in [h for h in app_logger.handlers if isinstance(h, LogTap)]:
        app_logger.removeHandler(h)
    state.INSTALLED = False
