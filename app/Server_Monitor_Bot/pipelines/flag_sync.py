# P8 플래그 동기화(3초, 모든 태스크): 만료 조정을 원복(행 단위로 한 태스크만)하고 runtime_flags를 컨테이너 SQLite 캐시로 복사한다.
from ..channels.telegram import render
from ..store.repos.base import epoch


def run(ctx, cache) -> list[dict]:
    repos = ctx.repos
    expired = repos.adjust.expire_due()
    adjusted = [r for r in expired if r["adjustment_id"]]
    if adjusted and ctx.telegram:
        ctx.telegram.send(None, render.adjust_expired(adjusted))
    cache.replace({r["key"]: (r["value"], epoch(r["expires_at"])) for r in repos.adjust.flags()})
    return expired
