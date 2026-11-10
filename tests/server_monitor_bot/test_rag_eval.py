# 검색 품질 평가: knowledge/eval/rag.json(질문→정답 id, 조정용 tune·시험용 holdout, 범위 밖 none)을 실제 임베딩으로 돌려 hit@1·hit@3·MRR, 기준값 통과율, 범위 밖 거절률을 출력한다. Qdrant URL과 임베딩 키가 있을 때만.
import json
import os
from pathlib import Path

import pytest

from app.Server_Monitor_Bot.config.settings import load
from app.Server_Monitor_Bot.docs.index import DocIndex
from app.Server_Monitor_Bot.docs.pipeline import sync_repo
from app.Server_Monitor_Bot.knowledge.schema import load_seed
from app.Server_Monitor_Bot.store.embed import Embedder
from app.Server_Monitor_Bot.store.repos.knowledge import Knowledge
from app.Server_Monitor_Bot.store.vector import VectorDB

QDRANT = os.environ.get("SERVER_BOT_EVAL_QDRANT_URL")
SET = Path(__file__).resolve().parents[2] / "app" / "Server_Monitor_Bot" / "knowledge" / "eval" / "rag.json"
pytestmark = pytest.mark.skipif(not (QDRANT and load().openai_key), reason="SERVER_BOT_EVAL_QDRANT_URL·임베딩 키 없음")


def ranked(item: dict, knowledge: Knowledge, docs: DocIndex, search: dict) -> list[tuple[str, float]]:
    kn = [(f"kn:{h['id']}", h["score"]) for h in knowledge.search(item["q"], limit=3, lexical=search.get("knowledge_lexical", 0.0))]
    dc = [(f"doc:{h['doc_id']}#{h['order']}", h["score"]) for h in docs.search(item["q"], limit=3, per_doc=2, lexical=search.get("docs_lexical", 0.0))]
    if item["kind"] == "knowledge":
        return kn
    if item["kind"] == "docs":
        return dc
    return sorted(kn + dc, key=lambda x: -x[1])[:3]


def gated(rows: list[dict], kn_min: float, doc_min: float) -> dict:
    pos = [r for r in rows if r["kind"] != "none"]
    neg = [r for r in rows if r["kind"] == "none"]
    floor = lambda r, ref: kn_min if ref.startswith("kn:") else doc_min
    ok = sum(any(ref in r["expect"] and score >= floor(r, ref) for ref, score in r["hits"]) for r in pos)
    rejected = sum(all(score < floor(r, ref) for ref, score in r["hits"]) for r in neg)
    return {"answer_at_min": round(ok / len(pos), 3) if pos else None, "reject_out_of_scope": round(rejected / len(neg), 3) if neg else None}


def report(rows: list[dict]) -> dict:
    n = len(rows)
    first = [next((i for i, (ref, _) in enumerate(r["hits"]) if ref in r["expect"]), None) for r in rows]
    out = {"n": n, "hit@1": sum(f == 0 for f in first) / n, "hit@3": sum(f is not None for f in first) / n,
           "mrr": sum(1 / (f + 1) for f in first if f is not None) / n, "thresholds": {}}
    for t in [x / 100 for x in range(20, 65, 5)]:
        kept = [(ref in r["expect"]) for r in rows for ref, score in r["hits"] if score >= t]
        found = sum(any(ref in r["expect"] and score >= t for ref, score in r["hits"]) for r in rows)
        p, rc = (sum(kept) / len(kept) if kept else 0.0), found / n
        out["thresholds"][t] = {"precision": round(p, 3), "recall": round(rc, 3), "f1": round(2 * p * rc / (p + rc), 3) if p + rc else 0.0}
    keep = [t for t, m in out["thresholds"].items() if m["recall"] >= 0.9] or list(out["thresholds"])
    out["recommended"] = max(keep, key=lambda t: (out["thresholds"][t]["f1"], t))
    return out


def test_rag_eval():
    settings = load()
    from qdrant_client import QdrantClient

    client = QdrantClient(url=QDRANT, api_key=os.environ.get("SERVER_BOT_EVAL_QDRANT_KEY"), https=False)
    vec = VectorDB(stage="evaltest", url=None, api_key=None, dim=settings.section("model").get("embedding_dim", 1536), client=client)
    embed = Embedder(settings.openai_key, settings.section("model").get("embedding", "text-embedding-3-small"))
    for name in ("knowledge", "docs"):
        vec.drop(name)
        vec.ensure(name)
    try:
        knowledge, docs = Knowledge(None, vec, embed, tuple(settings.policy)), DocIndex(vec, embed)
        items, seed_hash = load_seed()
        assert knowledge.upsert_seed(items, seed_hash)["inserted"] == len(items)
        assert not sync_repo(docs)["failed"]
        search = settings.section("search")
        rows = [{**item, "hits": ranked(item, knowledge, docs, search)} for item in json.loads(SET.read_text())]
        kn_min, doc_min = search.get("knowledge_min_score", 0.4), search.get("docs_min_score", 0.4)
        pos = [r for r in rows if r["kind"] != "none"]
        out = report(pos)
        splits = {s: {**{k: round(v, 3) for k, v in report([r for r in pos if r.get("split", "tune") == s]).items() if k in ("n", "hit@1", "hit@3", "mrr")},
                      **gated([r for r in rows if r.get("split", "tune") == s], kn_min, doc_min)} for s in ("tune", "holdout")}
        by_tag = {tag: report([r for r in pos if r["tag"] == tag])["hit@3"] for tag in sorted({r["tag"] for r in pos})}
        misses = [(r["q"], r["expect"], r["hits"][:1]) for r in pos if not any(ref in r["expect"] for ref, _ in r["hits"])]
        false_hits = [(r["q"], r["hits"][:1]) for r in rows if r["kind"] == "none" and gated([r], kn_min, doc_min)["reject_out_of_scope"] == 0]
        print(json.dumps({**out, "splits": splits, "hit@3_by_tag": by_tag, "misses": misses, "out_of_scope_hits": false_hits}, ensure_ascii=False, indent=1))
        assert out["hit@3"] >= 0.8
    finally:
        for name in ("knowledge", "docs"):
            vec.drop(name)
