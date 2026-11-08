# P4 지식·문서 적재: 배포 이미지 태그당 한 태스크가 seed.json과 저장소 문서(knowledge/docs)를 검사해 Qdrant에 반영한다. 설치 스크립트·P6도 sync를 쓴다.
import logging

from ..channels.telegram import render
from ..docs.pipeline import repo_done, sync_repo
from ..knowledge.schema import load_seed, validate

log = logging.getLogger("app.Server_Monitor_Bot.seed")


def sync(ctx, notify: bool = True) -> dict:
    repos, cfg = ctx.repos, ctx.settings.section("docs")
    try:
        items, seed_hash = load_seed()
    except Exception as exc:
        log.warning("seed read failed: error=%s", type(exc).__name__)
        if notify and ctx.telegram:
            ctx.telegram.send(None, render.seed_failed(type(exc).__name__))
        return {"error": type(exc).__name__}
    valid, broken = [], []
    for item in items:
        errors = validate(item, ctx.settings.policy)
        (broken.append((item.get("id", "?"), errors)) if errors else valid.append(item))
    stats = {"skipped": True} if repos.knowledge.seed_done(valid, seed_hash) else repos.knowledge.upsert_seed(valid, seed_hash)
    stats["reembedded"] = repos.knowledge.reembed_stale()
    stats["docs"] = {"skipped": True} if repo_done(repos.docs, **cfg) else sync_repo(repos.docs, **cfg)
    docs = stats["docs"]
    changed = stats.get("inserted") or stats.get("updated") or stats.get("index_failed") or docs.get("added") or docs.get("removed") or docs.get("failed")
    if notify and changed and ctx.telegram:
        ctx.telegram.send(None, render.seed(stats, broken, ctx.image_tag))
    log.info("seed synced: knowledge=%s docs=%s broken=%s", {k: v for k, v in stats.items() if k != "docs"}, docs, len(broken))
    return stats


def run(ctx) -> dict | None:
    name = f"server_bot:{ctx.stage}:seed:{ctx.image_tag}"[:64]
    conn = ctx.repos.db.lock_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 0) AS got", (name,))
            if not cur.fetchone()["got"]:
                return None
        try:
            return sync(ctx)
        except Exception as exc:
            log.warning("seed sync failed: error=%s", type(exc).__name__)
            return None
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK(%s)", (name,))
        finally:
            conn.close()
