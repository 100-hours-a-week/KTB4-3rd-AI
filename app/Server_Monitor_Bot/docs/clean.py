# 3단계 정리: 비밀 값 가리기(mask), 공백 정리, 빈 청크 빼기. 순서와 sha를 다시 매긴다.
import re
from dataclasses import replace

from ..hooks.mask import mask
from .chunk import sha
from .models import Chunk


def clean(chunks: list[Chunk]) -> list[Chunk]:
    out = []
    for c in chunks:
        text = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+\n", "\n", mask(c.text) or "")).strip()
        if text:
            out.append(replace(c, text=text, order=len(out), sha=sha(c.section, text)))
    return out
