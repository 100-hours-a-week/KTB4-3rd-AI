# 영수증 사진 URL을 받아 JPEG, PNG인지 검사하고 바이트를 돌려준다.
from io import BytesIO
from time import monotonic
from urllib.parse import urlparse

import httpx
from PIL import Image

from ...core.exceptions import ImageFetchError, ImageSizeError, ImageTypeError
from ...core.logging import get_logger, http_reason
from ...llm import ImageBytes

logger = get_logger(__name__)

ALLOWED = {"image/jpeg", "image/png"}
MAX_SIZE = 5 * 1024 * 1024
TIMEOUT = 5.0


def url_host(image_url: str) -> str:
    try:
        return urlparse(image_url).netloc or "unknown"
    except ValueError:
        return "unknown"


def download(image_url: str, host: str) -> bytes:
    # 명세의 5초는 요청 전체 기준이다. httpx timeout은 연결·읽기 단계마다 따로 걸리므로 받는 동안 남은 시간을 직접 본다.
    # ponytail: 조각 사이에서만 확인하므로 최악은 5초 + 읽기 한 번(5초). 더 엄격해야 하면 별도 스레드로 끊는다.
    deadline = monotonic() + TIMEOUT
    chunks, size = [], 0
    with httpx.stream("GET", image_url, timeout=TIMEOUT, follow_redirects=False) as res:
        res.raise_for_status()
        for chunk in res.iter_bytes():
            size += len(chunk)
            if size > MAX_SIZE:
                logger.warning("image size is invalid: host=%s bytes>%s", host, MAX_SIZE)
                raise ImageSizeError()
            if monotonic() > deadline:
                raise httpx.ReadTimeout("image download exceeded total timeout")
            chunks.append(chunk)
    return b"".join(chunks)


def load_image(image_url: str) -> ImageBytes:
    host = url_host(image_url)
    started = monotonic()
    try:
        content = download(image_url, host)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        ms = int((monotonic() - started) * 1000)
        logger.warning("image download failed: host=%s reason=%s status=%s ms=%s", host, http_reason(exc), status, ms)
        raise ImageFetchError() from exc

    if not content:
        logger.warning("image size is invalid: host=%s bytes=0", host)
        raise ImageSizeError()

    try:
        with Image.open(BytesIO(content)) as img:
            width, height = img.size
            media_type = Image.MIME[img.format]
    except Exception as exc:
        logger.warning("image must be jpeg or png: host=%s reason=not_image bytes=%s", host, len(content))
        raise ImageTypeError() from exc

    if media_type not in ALLOWED:
        logger.warning("image must be jpeg or png: host=%s reason=wrong_type type=%s", host, media_type)
        raise ImageTypeError()

    ms = int((monotonic() - started) * 1000)
    logger.info("image loaded: host=%s bytes=%s type=%s ms=%s", host, len(content), media_type, ms)
    return ImageBytes(
        content=content,
        width=width,
        height=height,
        media_type=media_type,
        source=image_url,
    )
