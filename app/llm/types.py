from dataclasses import dataclass
from typing import Literal

ModelName = Literal["openai", "slm", "ocr"]


@dataclass(frozen=True)
class ImageBytes:
    content: bytes
    width: int
    height: int
    media_type: str
    source: str


@dataclass(frozen=True)
class OcrSpan:
    text: str
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class OcrResult:
    spans: tuple[OcrSpan, ...]
    numbers: tuple[int, ...]