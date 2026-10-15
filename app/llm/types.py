from dataclasses import dataclass
from typing import Literal

ModelName = Literal["api_1", "api_2", "ocr", "slm"]


@dataclass(frozen=True)
class ImageBytes:
    content: bytes
    width: int
    height: int
    media_type: str
    source: str