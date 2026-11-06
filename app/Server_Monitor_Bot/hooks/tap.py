# app 로거에 붙는 LogTap. 기존 로그 줄을 큐에 넣기만 한다(가득 차면 버리고 dropped+1).
import logging
import queue
import time

from . import state

SUFFIX = " request_id="


class LogTap(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name.startswith("app.Server_Monitor_Bot"):
                return
            message = record.getMessage()
            rid = getattr(record, "request_id", None)
            if rid and message.endswith(SUFFIX + rid):
                message = message[: -len(SUFFIX + rid)]
            exc = self.formatter.formatException(record.exc_info) if record.exc_info and self.formatter else None
            if record.exc_info and not exc:
                exc = logging.Formatter().formatException(record.exc_info)
            state.QUEUE.put_nowait(("log", {"ts": record.created or time.time(), "level": record.levelname,
                                            "logger": record.name, "message": message, "request_id": rid, "exc": exc}))
        except queue.Full:
            state.STATS["dropped"] += 1
        except Exception:
            pass
