# P4 지식 시드 적재: 배포 이미지 태그당 한 태스크가 seed.json을 검사해 MySQL·Qdrant에 반영한다.
import logging

from ..channels.telegram import render
from ..knowledge.schema import load_seed, validate

log = logging.getLogger("app.Server_Monitor_Bot.seed")


def run(ctx) -> dict | None:
    repos = ctx.repos
    name = f"server_bot:{ctx.stage}:seed:{ctx.image_tag}"[:64]
    conn = repos.db.lock_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 0) AS got", (name,))
            if not cur.fetchone()["got"]:
                return None
        try:
            items, seed_hash = load_seed()
        except Exception as exc:
            log.warning("seed read failed: error=%s", type(exc).__name__)
            if ctx.telegram:
                ctx.telegram.send(None, render.seed_failed(type(exc).__name__))
            return None
        valid, broken = [], []
        for item in items:
            errors = validate(item, ctx.settings.policy)
            (broken.append((item.get("id", "?"), errors)) if errors else valid.append(item))
        if repos.knowledge.seed_done(valid, seed_hash):
            return {"skipped": True}
        stats = repos.knowledge.upsert_seed(valid, seed_hash)
        if (stats["inserted"] or stats["updated"] or stats["index_failed"]) and ctx.telegram:
            ctx.telegram.send(None, render.seed(stats, broken, ctx.image_tag))
        log.info("seed loaded: inserted=%s updated=%s broken=%s", stats["inserted"], stats["updated"], len(broken))
        return stats
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK(%s)", (name,))
        finally:
            conn.close()
