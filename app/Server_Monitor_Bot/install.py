# 설치 스크립트: python -m app.Server_Monitor_Bot.install [--check|--reembed]. 스키마(없을 때만) → Qdrant 컬렉션 → 지식 시드·저장소 문서(P4와 같은 sync).
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from .config.settings import load
from .docs.index import text_of as doc_text
from .docs.models import Chunk
from .store.repos.knowledge import embed_text, key
from .store.vector import COLLECTIONS

SCHEMA = Path(__file__).with_name("store") / "schema.sql"
ENV_NAMES = ("AI_DB_URL", "AI_QDRANT_URL", "AI_QDRANT_API_KEY", "TELEGRAM_BOT_TOKEN", "Admin_Telegram_ID")
BATCH = 64


def env_report(env=os.environ) -> dict:
    found = {name: bool(env.get(name)) for name in ENV_NAMES}
    found["OPENAI_API_KEY_MONITOR|OPENAI_API_KEY_1|OPENAI_API_KEY"] = any(env.get(n) for n in ("OPENAI_API_KEY_MONITOR", "OPENAI_API_KEY_1",
                                                                                               "OPENAI_API_KEY"))
    return found


def statements(sql: str) -> list[str]:
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def has_schema(db) -> bool:
    return db.fetch_one("SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'schema_version'")["n"] > 0


def apply_schema(db) -> bool:
    if has_schema(db):
        return False
    for stmt in statements(SCHEMA.read_text()):
        db.execute(stmt)
    return True


def counts(vec) -> dict:
    return {name: vec.c.count(vec.name(name), exact=True).count if vec.c.collection_exists(vec.name(name)) else None for name in COLLECTIONS}


def point_key(name: str, p: dict) -> str:
    if name == "docs":
        return p["key"]
    return key(p["id"], p["version"]) if p.get("status") == "archived" else key(p["id"])


def point_text(name: str, p: dict) -> str:
    if name == "docs":
        return doc_text(Chunk(p["doc_id"], p["title"], p["section"], p["order"], p["text"], p["sha"]))
    return embed_text(p)


def reembed(vec, embed, chat_repo, backup_dir: Path) -> dict:
    data = {name: vec.scroll(name) for name in ("knowledge", "docs")}
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"reembed-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, default=str))
    vectors = {}
    for name, rows in data.items():
        texts = [point_text(name, p) for p in rows]
        vectors[name] = [v for i in range(0, len(texts), BATCH) for v in embed.many(texts[i:i + BATCH])]
    for name, rows in data.items():
        vec.drop(name)
        vec.ensure(name)
        for i in range(0, len(rows), BATCH):
            vec.upsert_many(name, [(point_key(name, p), v, p) for p, v in zip(rows[i:i + BATCH], vectors[name][i:i + BATCH])])
    vec.drop("chat")
    vec.ensure("chat")
    return {"backup": str(path), "knowledge": len(data["knowledge"]), "docs": len(data["docs"]), "chat_pending": chat_repo.mark_all_pending()}


def run(ctx, *, check: bool = False, redo: bool = False, out=print) -> int:
    vec = ctx.repos.knowledge.vec
    out("환경 변수: " + ", ".join(f"{k} {'있음' if v else '없음'}" for k, v in env_report().items()))
    if check:
        out(f"MySQL 스키마: {'있음' if has_schema(ctx.repos.db) else '없음'}")
        out(f"Qdrant 건수: {counts(vec)}")
        return 0
    out(f"MySQL 스키마: {'새로 적용' if apply_schema(ctx.repos.db) else '이미 있음'}")
    for name in COLLECTIONS:
        vec.ensure(name)
    if redo:
        out(f"다시 임베딩: {reembed(vec, ctx.repos.knowledge.embed, ctx.repos.chat, ctx.settings.buffer_dir)}")
    from .pipelines.knowledge_seed import sync

    stats = sync(ctx, notify=False)
    out(f"지식·문서: {stats}")
    out(f"Qdrant 건수: {counts(vec)}")
    failed = "error" in stats or stats.get("index_failed") or (stats.get("docs") or {}).get("failed")
    return 1 if failed else 0


def main(argv=None) -> int:
    args = argparse.ArgumentParser(prog="python -m app.Server_Monitor_Bot.install")
    args.add_argument("--check", action="store_true", help="쓰기 없이 연결·스키마·컬렉션 건수만 확인")
    args.add_argument("--reembed", action="store_true", help="임베딩 모델·차원을 바꾼 뒤 knowledge·docs를 백업하고 다시 임베딩")
    opts = args.parse_args(argv)
    settings = load()
    if not settings.db_url:
        print("AI_DB_URL이 없습니다", file=sys.stderr)
        return 1
    if not settings.stage:
        print("AI_DB_URL의 DB 이름이 ai_<local|dev|stg|prod> 형식이 아닙니다", file=sys.stderr)
        return 1
    from .runtime.context import Ctx
    from .store.embed import Embedder
    from .store.mysql import MySQLDB
    from .store.repos import Repos
    from .store.vector import VectorDB

    model = settings.section("model")
    vec = VectorDB(stage=settings.stage, url=settings.qdrant_url, api_key=settings.qdrant_key, dim=model.get("embedding_dim", 1536))
    embed = Embedder(settings.openai_key, model.get("embedding", "text-embedding-3-small"))
    repos = Repos(MySQLDB.from_url(settings.db_url, max_connections=2), vec, embed, tuple(settings.policy))
    return run(Ctx(settings=settings, repos=repos, image_tag="install"), check=opts.check, redo=opts.reembed)


if __name__ == "__main__":
    sys.exit(main())
