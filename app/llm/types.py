from dataclasses import dataclass
from typing import Literal

ModelName = Literal["api", "ocr", "slm"]


@dataclass(frozen=True)
class ImageBytes:
    content: bytes
    width: int
    height: int
    media_type: str
    source: str