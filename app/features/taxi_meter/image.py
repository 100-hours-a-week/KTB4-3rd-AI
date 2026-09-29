# 미터기 사진 URL을 받아 JPEG, PNG인지 검사하고 바이트를 돌려준다.
from io import BytesIO
from urllib.parse import urlparse

import httpx
from PIL import Image

from ...core.exceptions import ImageFetchError, ImageSizeError, ImageTypeError
from ...core.logging import get_logger
from ...llm import ImageBytes

logger = get_logger(__name__)

ALLOWED = {"image/jpeg", "image/png"}
MAX_SIZE = 5 * 1024 * 1024
TIMEOUT = 5.0


def url_host(image_url: str) -> str:
    return urlparse(image_url).netloc or "unknown"


def load_image(image_url: str) -> ImageBytes:
    host = url_host(image_url)
    try:
        res = httpx.get(image_url, timeout=TIMEOUT, follow_redirects=False)
        res.raise_for_status()
    except httpx.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        logger.warning("image download failed: host=%s status=%s", host, status)
        raise ImageFetchError() from exc

    content = res.content
    if not content or len(content) > MAX_SIZE:
        logger.warning("image size is invalid: host=%s bytes=%s", host, len(content))
        raise ImageSizeError()

    try:
        with Image.open(BytesIO(content)) as img:
            width, height = img.size
            media_type = Image.MIME[img.format]
    except Exception as exc:
        logger.warning("image must be jpeg or png: host=%s", host)
        raise ImageTypeError() from exc

    if media_type not in ALLOWED:
        logger.warning("image must be jpeg or png: host=%s type=%s", host, media_type)
        raise ImageTypeError()

    logger.info("image loaded: host=%s bytes=%s type=%s", host, len(content), media_type)
    return ImageBytes(
        content=content,
        width=width,
        height=height,
        media_type=media_type,
        source=image_url,
    )
