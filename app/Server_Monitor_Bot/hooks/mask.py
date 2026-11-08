# 저장 전 마스킹: URL 쿼리스트링과 token·key·secret·signature·authorization·password 값을 지운다.
import re

QUERY = re.compile(r"(https?://[^\s?]+)\?\S*")
SECRET = re.compile(r"(?i)\b(token|key|secret|signature|authorization|password|api_key|x-amz-[a-z-]+)=([^\s&]+)")
BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._-]+")
OPENAI = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")


def mask(text: str | None) -> str | None:
    if not text:
        return text
    text = QUERY.sub(r"\1?<masked>", text)
    text = SECRET.sub(r"\1=<masked>", text)
    text = BEARER.sub(r"\1 <masked>", text)
    return OPENAI.sub("<masked>", text)
