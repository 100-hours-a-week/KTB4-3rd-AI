# 4단계 중복 확인: 이미 넣은 절과 비교해 새것·같음·겹침·삭제로 나눈다. 겹침(다른 문서와 거의 같음)도 저장하고 표시만 한다.
from .models import Chunk, Document, Plan


def dedupe(doc: Document, chunks: list[Chunk], index, *, overlap: float = 0.95) -> Plan:
    old = {p["key"]: p for p in index.chunks_of(doc.doc_id)}
    keys = {c.key for c in chunks}
    new = [c for c in chunks if c.key not in old]
    vectors = dict(zip((c.key for c in new), index.embed(new))) if new else {}
    found = []
    for c in new:
        hit = index.similar(vectors[c.key], doc.doc_id)
        if hit and hit["score"] >= overlap:
            found.append((c, f"{hit['doc_id']}#{hit['order']}"))
    return Plan(doc, new, [c for c in chunks if c.key in old], found, [k for k in old if k not in keys], vectors)
