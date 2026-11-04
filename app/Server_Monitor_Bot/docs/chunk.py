# 2단계 나누기: Markdown → 절(#·##) 단위 청크. 절이 max_chars보다 길거나 제목이 없으면 문단 단위로 나눈다.
import hashlib
import re

from .models import Chunk, Document

HEAD = re.compile(r"^(#{1,2})\s+(.+?)\s*#*\s*$")


def sha(section: str, text: str) -> str:
    return hashlib.sha256(f"{section}\n{text}".encode()).hexdigest()[:16]


def sections(markdown: str) -> list[tuple[str, str]]:
    out, name, lines, fenced = [], "", [], False
    for line in markdown.splitlines():
        fenced ^= line.lstrip().startswith("```")
        m = None if fenced else HEAD.match(line)
        if m:
            out.append((name, "\n".join(lines)))
            name, lines = m.group(2), []
        else:
            lines.append(line)
    out.append((name, "\n".join(lines)))
    return [(n, t.strip()) for n, t in out if t.strip()]


def pieces(text: str, max_chars: int) -> list[str]:
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        while len(para) > max_chars:
            if cur:
                out.append(cur)
                cur = ""
            out.append(para[:max_chars])
            para = para[max_chars:]
        if cur and len(cur) + len(para) + 2 > max_chars:
            out.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    return [p for p in (*out, cur) if p.strip()]


def split(doc: Document, *, max_chars: int = 1500) -> list[Chunk]:
    chunks = []
    for name, text in sections(doc.markdown):
        section = name or doc.title
        for part in pieces(text, max_chars):
            chunks.append(Chunk(doc.doc_id, doc.title, section, len(chunks), part, sha(section, part)))
    return chunks
