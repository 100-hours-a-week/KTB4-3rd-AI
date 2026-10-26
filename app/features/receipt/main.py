# 영수증 요청 실행 함수(run_api, run_gpu) 정의
# 배분은 inner_queue가 정한다. 여기서는 경로별 실행만 한다.
from ...core.config import DEVICE
from ...core.exceptions import InternalError, ModelNotReady, UpstreamError
from ...core.logging import get_logger
from ...llm import pipe
from .image import load_image
from .pipeline import model_api_run, model_pipe_run

logger = get_logger(__name__)

KIND = "receipt"


async def run_api(image_url: str) -> dict[str, int | str | None]:
    return await luna(await load_image(image_url), "route_api")


async def run_gpu(image_url: str) -> dict[str, int | str | None]:
    image = await load_image(image_url)
    if DEVICE == "off":
        return await luna(image, "device_off")
    try:
        result = await (pipe(KIND, image) if DEVICE == "modal" else model_pipe_run(image, DEVICE))
    except ModelNotReady:
        # Modal 배포 중이거나 기동 때 준비 실패로 이미 기록된 장치다. 요청마다 경고하지 않는다.
        return await luna(image, "model_not_ready")
    except (InternalError, UpstreamError) as exc:
        logger.warning("image model failed: code=%s device=%s", exc.code, DEVICE)
        return await luna(image, exc.code.lower())
    if result is None:
        return await luna(image, "judge_fallback")
    logger.info("request route: route=pipe device=%s", DEVICE)
    return result


async def luna(image, reason: str) -> dict[str, int | str | None]:
    logger.info("request route: route=luna reason=%s", reason)
    return await model_api_run(image)
