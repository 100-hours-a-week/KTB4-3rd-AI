# 분석 프로세스 실행 컨텍스트: 설정·저장소·텔레그램·후속 이벤트 적재를 한 객체로 넘긴다.
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("app.Server_Monitor_Bot")


def new_event(type_: str, payload: dict | None = None, *, source: str = "followup", from_id: int | None = None,
              chat_id: int | None = None, chain_depth: int = 0, event_id: str | None = None) -> dict:
    return {"id": event_id or uuid.uuid4().hex, "type": type_, "source": source, "at": time.time(), "payload": payload or {},
            "from_id": from_id, "chat_id": chat_id, "chain_depth": chain_depth}


@dataclass
class Ctx:
    settings: Any
    repos: Any
    telegram: Any = None
    task_id: str = "local"
    image_tag: str = "unknown"
    enqueue: Callable[[dict, float | None], None] = lambda event, not_before=None: None
    is_leader: Callable[[], bool] = lambda: False
    stats: dict = field(default_factory=lambda: {"send_fail": 0, "restarts": 0})

    @property
    def stage(self) -> str:
        return self.settings.stage

    def head(self) -> str:
        return f"[{self.stage}·{self.task_id[:6]}]"
