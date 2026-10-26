# 도구 등록: 이름 → 도구, 기능별 묶음, 도구가 쓸 실행 컨텍스트와 훈련 상태(숨김 지식·훈련 집계).
from contextvars import ContextVar

_ctx = None
hide_knowledge: ContextVar[tuple[str, ...]] = ContextVar("hide_knowledge", default=())
drill_mode: ContextVar[bool] = ContextVar("drill_mode", default=False)


def bind(ctx) -> None:
    global _ctx
    _ctx = ctx


def ctx():
    if _ctx is None:
        raise RuntimeError("tools not bound")
    return _ctx


def tools() -> dict:
    from . import raw, work

    return {t.name: t for t in (*work.ALL, *raw.ALL)}


def bundle(name: str) -> list[str]:
    from . import raw, work

    work_names = [t.name for t in work.ALL]
    if name == "S1":
        return [*work_names, "recent_logs", "read_code"]
    return [*work_names, *[t.name for t in raw.ALL]]
