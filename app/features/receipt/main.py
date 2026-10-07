# 영수증 요청 실행 함수(run) 정의
import threading
from contextlib import nullcontext

from ...core.config import IMAGE_DEVICE, LOCAL_DEVICES
from ...core.exceptions import InternalError, ModelNotReady, UpstreamError
from ...core.logging import get_logger
from .image import load_image
from .pipeline import model_api_run, model_pipe_run

logger = get_logger(__name__)

gate = threading.Lock() if IMAGE_DEVICE in LOCAL_DEVICES else nullcontext()


def run(image_url: str) -> dict[str, int | str]:
    image = load_image(image_url)
    if IMAGE_DEVICE == "off":
        return model_api_run(image)
    try:
        with gate:
            result = model_pipe_run(image, IMAGE_DEVICE)
    except (ModelNotReady, InternalError, UpstreamError) as exc:
        logger.warning("image model failed: %s", exc.code)
        result = None
    if result is None:
        return model_api_run(image)
    return result
