# 문서 컬렉션(<stage>_docs) 하나만 다룬다: 목록·절 조회·쓰기·지우기·의미 검색. 원본이 Qdrant다.
from datetime import datetime, timezone

from ..store.rerank import rerank
from .chunk import sha
from .models import Chunk, Document

C = "docs"
BATCH = 64


def text_of(c: Chunk) -> str:
    return f"{c.title} > {c.section}\n{c.text}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DocIndex:
    def __init__(self, vec, embedder):
        self.vec, self.embedder = vec, embedder

    def chunks_of(self, doc_id: str) -> list[dict]:
        return sorted(self.vec.scroll(C, equals={"doc_id": doc_id}), key=lambda p: p.get("order", 0))

    def docs(self) -> list[dict]:
        found: dict[str, dict] = {}
        for p in self.vec.scroll(C):
            d = found.setdefault(p["doc_id"], {"doc_id": p["doc_id"], "title": p.get("title"), "origin": p.get("origin"),
                                               "sections": 0, "updated_at": p.get("updated_at")})
            d["sections"] += 1
            d["updated_at"] = max(d["updated_at"] or "", p.get("updated_at") or "")
        return sorted(found.values(), key=lambda d: d["doc_id"])

    def get(self, key: str) -> dict | None:
        return self.vec.payloads(C, [key]).get(key)

    def chunk(self, doc_id: str, order: int) -> dict | None:
        return next((p for p in self.chunks_of(doc_id) if p.get("order") == order), None)

    def embed(self, chunks: list[Chunk]) -> list[list[float]]:
        texts = [text_of(c) for c in chunks]
        return [v for i in range(0, len(texts), BATCH) for v in self.embedder.many(texts[i:i + BATCH])]

    def similar(self, vector: list[float], doc_id: str) -> dict | None:
        hits = self.vec.search(C, vector, limit=1, none_of={"doc_id": [doc_id]})
        return hits[0] if hits else None

    def payload(self, c: Chunk, doc: Document) -> dict:
        return {"key": c.key, "doc_id": c.doc_id, "title": c.title, "section": c.section, "order": c.order, "text": c.text, "sha": c.sha,
                "origin": doc.origin, "by": str(doc.by), "updated_at": now(), "status": "active"}

    def write(self, chunks: list[Chunk], vectors: list[list[float]], doc: Document) -> None:
        self.vec.upsert_many(C, [(c.key, v, self.payload(c, doc)) for c, v in zip(chunks, vectors)])

    def reorder(self, chunks: list[Chunk]) -> None:
        for c in chunks:
            self.vec.set_payload(C, c.key, {"order": c.order})

    def delete_keys(self, keys: list[str]) -> None:
        self.vec.delete_ids(C, keys)

    def delete_doc(self, doc_id: str) -> int:
        keys = [p["key"] for p in self.chunks_of(doc_id)]
        self.delete_keys(keys)
        return len(keys)

    def replace(self, key: str, text: str, by) -> str | None:
        old = self.get(key)
        if not old:
            return None
        c = Chunk(old["doc_id"], old["title"], old["section"], old["order"], text, sha(old["section"], text))
        doc = Document(c.doc_id, c.title, "", old.get("origin", "telegram"), by)
        self.write([c], self.embed([c]), doc)
        if c.key != key:
            self.delete_keys([key])
        return c.key

    def search(self, query: str, *, limit: int = 5, min_score: float = 0.0, per_doc: int = 2, vector: list[float] | None = None,
               lexical: float = 0.0) -> list[dict]:
        hits = self.vec.search(C, vector or self.embedder.one(query), limit=limit * per_doc * (2 if lexical > 0 else 1), equals={"status": "active"})
        hits = rerank(query, [h for h in hits if h["score"] >= min_score], lambda h: f"{h.get('title', '')} > {h.get('section', '')}",
                      lambda h: h.get("text", ""), lexical)
        out, seen = [], {}
        for h in hits:
            if h["score"] < min_score or seen.get(h["doc_id"], 0) >= per_doc:
                continue
            seen[h["doc_id"]] = seen.get(h["doc_id"], 0) + 1
            out.append(h)
        return out[:limit]
