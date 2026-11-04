# 도구 등록: 이름 → 도구, 기능별 묶음(지난 대화 검색은 빼고, CloudWatch는 AWS 자격 증명이 있을 때만), 도구가 쓸 실행 컨텍스트와 훈련 상태(숨김 지식·훈련 집계).
import functools
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


@functools.cache
def aws() -> bool:
    try:
        import boto3

        return boto3.Session().get_credentials() is not None
    except Exception:
        return False


def bundle(name: str) -> list[str]:
    from . import raw, work

    skip = {"search_chat"} | (set() if aws() else {"query_cloudwatch"})
    work_names = [t.name for t in work.ALL if t.name not in skip]
    if name == "S1":
        return [*work_names, "recent_logs", "read_code"]
    return [*work_names, *[t.name for t in raw.ALL]]
