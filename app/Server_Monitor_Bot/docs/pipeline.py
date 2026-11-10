# 문서 넣기 파이프라인: convert → split → clean → dedupe → apply. 저장소 문서(origin=repo) 동기화도 같은 길을 쓴다.
from pathlib import Path

from .chunk import split
from .clean import clean
from .convert import convert, doc_id
from .dedupe import dedupe
from .index import DocIndex
from .models import Plan, Result, Source

REPO = Path(__file__).resolve().parents[1] / "knowledge" / "docs"


def apply(plan: Plan, index: DocIndex) -> Result:
    index.write(plan.new, [plan.vectors[c.key] for c in plan.new], plan.doc)
    index.reorder(plan.same)
    index.delete_keys(plan.removed)
    return Result(plan.doc.doc_id, plan.doc.title, plan.doc.origin, len(plan.new), len(plan.same),
                  [f"{c.section} → {ref}" for c, ref in plan.overlap], len(plan.removed))


def ingest(source: Source, index: DocIndex, *, max_mb: int = 20, timeout_s: int = 60, chunk_chars: int = 1500,
           overlap: float = 0.95) -> Result:
    doc = convert(source, max_mb=max_mb, timeout_s=timeout_s)
    if source.origin != "repo" and any(p.get("origin") == "repo" for p in index.chunks_of(doc.doc_id)):
        raise ValueError(f"저장소 문서와 이름이 같습니다({doc.doc_id}). 다른 이름으로 올려 주세요")
    chunks = clean(split(doc, max_chars=chunk_chars))
    if not chunks:
        raise ValueError("본문이 비어 있습니다")
    return apply(dedupe(doc, chunks, index, overlap=overlap), index)


def repo_keys(folder: Path, chunk_chars: int) -> set[str]:
    keys = set()
    for path in sorted(folder.glob("*.md")):
        doc = convert(Source(path.name, path.read_bytes(), "repo", "repo"))
        keys |= {c.key for c in clean(split(doc, max_chars=chunk_chars))}
    return keys


def repo_done(index: DocIndex, folder: Path = REPO, *, chunk_chars: int = 1500, **_) -> bool:
    have = {p["key"] for p in index.vec.scroll("docs", equals={"origin": "repo"})}
    return have == repo_keys(folder, chunk_chars)


def sync_repo(index: DocIndex, folder: Path = REPO, **cfg) -> dict:
    stats = {"docs": 0, "added": 0, "removed": 0, "failed": []}
    paths = sorted(folder.glob("*.md"))
    for path in paths:
        try:
            r = ingest(Source(path.name, path.read_bytes(), "repo", "repo"), index, **cfg)
            stats["docs"] += 1
            stats["added"] += r.added
            stats["removed"] += r.removed
        except Exception as exc:
            stats["failed"].append(f"{path.stem}: {type(exc).__name__}")
    gone = {d["doc_id"] for d in index.docs() if d["origin"] == "repo"} - {doc_id(p.name) for p in paths}
    for d in sorted(gone):
        stats["removed"] += index.delete_doc(d)
    return stats
