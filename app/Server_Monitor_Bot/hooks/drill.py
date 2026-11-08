# drill_point(name): dev·local에서 request_id가 drill-로 시작하는 요청에만 주입 플래그(drill.<name>)를 돌려준다.
from . import state
from .flags import flag


def drill_point(name: str) -> dict | None:
    if state.STAGE not in ("local", "dev"):
        return None
    from ...core.logging import request_id

    rid = request_id.get() or ""
    if not rid.startswith("drill-"):
        return None
    value = flag(f"drill.{name}")
    return value if isinstance(value, dict) else None
