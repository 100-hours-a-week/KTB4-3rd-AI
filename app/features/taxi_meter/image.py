from io import BytesIO

import httpx
from PIL import Image

from ...core.exceptions import ImageFetchError
from ...llm.client import ImageBytes

ALLOWED = {"image/jpeg", "image/png"}
MAX_SIZE = 5 * 1024 * 1024
TIMEOUT = 5.0


def load_image(image_url: str) -> ImageBytes:
    try:
        res = httpx.get(image_url, timeout=TIMEOUT, follow_redirects=False)
        res.raise_for_status() # 예외처리, 4xx, 5xx 예외처리 
    except httpx.HTTPError as exc:
        raise ImageFetchError("image download failed") from exc

    content = res.content
    if not content or len(content) > MAX_SIZE:
        raise ImageFetchError("image size is invalid")

    try:
        with Image.open(BytesIO(content)) as img:
            width, height = img.size
            media_type = Image.MIME[img.format]
    except Exception as exc:
        raise ImageFetchError("image must be jpeg or png") from exc

    if media_type not in ALLOWED:
        raise ImageFetchError("image must be jpeg or png")

    return ImageBytes(
        content=content,
        width=width,
        height=height,
        media_type=media_type,
        source=image_url,
    )