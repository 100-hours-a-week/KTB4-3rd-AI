# 문서 파이프라인 단계 사이 데이터: Source → Document → Chunk → Plan → Result.
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Source:
    name: str
    data: bytes
    origin: str = "telegram"
    by: str = "-"


@dataclass(frozen=True)
class Document:
    doc_id: str
    title: str
    markdown: str
    origin: str
    by: str


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    title: str
    section: str
    order: int
    text: str
    sha: str

    @property
    def key(self) -> str:
        return f"doc:{self.doc_id}:{self.sha}"


@dataclass
class Plan:
    doc: Document
    new: list[Chunk] = field(default_factory=list)
    same: list[Chunk] = field(default_factory=list)
    overlap: list[tuple[Chunk, str]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    vectors: dict[str, list[float]] = field(default_factory=dict)


@dataclass(frozen=True)
class Result:
    doc_id: str
    title: str
    origin: str
    added: int
    same: int
    overlap: list[str]
    removed: int
