# 미터기 요청 실행(run) 정의
import threading
from contextlib import nullcontext

from ...core.config import DEVICE, LOCAL_DEVICES
from ...core.exceptions import InternalError, ModelNotReady, UpstreamError
from ...core.logging import get_logger
from .image import load_image
from .pipeline import model_api_run, model_pipe_run

logger = get_logger(__name__)


gate = threading.Lock() if DEVICE in LOCAL_DEVICES else nullcontext()


def run(image_url: str) -> dict[str, int]:
    image = load_image(image_url)
    if DEVICE == "off":
        return luna(image, "device_off")
    try:
        with gate:
            result = model_pipe_run(image, DEVICE)
    except ModelNotReady:
        # 기동 때 준비 실패로 이미 기록된 장치다. 요청마다 경고하지 않는다.
        return luna(image, "model_not_ready")
    except (InternalError, UpstreamError) as exc:
        logger.warning("image model failed: code=%s device=%s", exc.code, DEVICE)
        return luna(image, exc.code.lower())
    if result is None:
        return luna(image, "judge_fallback")
    logger.info("request route: route=pipe device=%s", DEVICE)
    return result


def luna(image, reason: str) -> dict[str, int]:
    logger.info("request route: route=luna reason=%s", reason)
    return model_api_run(image)
