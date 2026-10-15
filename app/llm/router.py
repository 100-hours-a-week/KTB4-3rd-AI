# llm(모델명, 프롬프트, 이미지, device, schema)은 모델을 한 번 돌리고 결과만 돌려준다.
# - ocr:    image → 읽은 원문 str
# - slm:    prompt=[(원문, 가설), ...] → 가설마다 [모순, 지지, 중립]
# - api_1:  prompt, image → JSON 문자열. 이미지용 OpenAI 키와 모델(OPENAI_*_1)
# - api_2:  prompt, image → JSON 문자열. 혼잡도용 OpenAI 키와 모델(OPENAI_*_2)
# device가 "modal"이면 ocr과 slm은 Modal T4에서 돈다. 반환 형식은 같다.
# 순서 제어와 파이프라인 구성은 기능 폴더가 맡는다.
# 준비(로드, 키, Modal 조회)에 실패한 (모델, device)는 down에 넣고, 이후 호출은 바로 ModelNotReady를 낸다.
# 기능 폴더가 이를 받아 luna로 넘기므로 실패 비용을 요청마다 다시 치르지 않는다.
import threading
from collections.abc import Callable
from typing import Any

from ..core.exceptions import InternalError, ModelNotReady
from ..core.logging import get_logger
from .providers.modal_gpu import ModalEngine
from .providers.ocr import OcrEngine
from .providers.openai import OpenAIEngine
from .providers.slm import SlmEngine
from .types import ImageBytes, ModelName

logger = get_logger(__name__)

Engine = OpenAIEngine | ModalEngine | OcrEngine | SlmEngine
engines: dict[tuple[str, str], Engine] = {}
lock = threading.Lock()
# ponytail: 재시작 전까지 고정이다. 중간 복구가 필요하면 실패 시각을 기록해 일정 시간 뒤 다시 시도한다.
down: set[tuple[str, str]] = set()


def engine(name: ModelName, device: str) -> Engine:
    key = (name, device)
    with lock:
        if key not in engines:
            if name in ("api_1", "api_2"):
                engines[key] = OpenAIEngine(name[-1])
            elif name in ("ocr", "slm") and device == "modal":
                engines[key] = ModalEngine()
            elif name == "ocr":
                engines[key] = OcrEngine(device)
            elif name == "slm":
                engines[key] = SlmEngine(device)
            else:
                logger.error("unknown model: %s", name)
                raise InternalError()
        return engines[key]


def ready(name: ModelName, device: str, call: Callable[[], Any]) -> Any:
    key = (name, device)
    if key in down:
        raise ModelNotReady()
    try:
        return call()
    except ModelNotReady:
        # api는 키 확인 비용이 없어 차단하지 않는다. 이미지 모델(ocr, slm)만 기록한다.
        if name in ("ocr", "slm") and key not in down:
            down.add(key)
            logger.warning("model marked down: model=%s device=%s", name, device)
        raise


def load(name: ModelName, device: str = "cpu") -> None:
    ready(name, device, lambda: engine(name, device).load())


def llm(
    name: ModelName,
    prompt: str | list[tuple[str, str]] | None = None,
    image: ImageBytes | None = None,
    device: str = "cpu",
    schema: dict | None = None,
) -> str | list[list[float]]:
    def call() -> str | list[list[float]]:
        model = engine(name, device)
        if name == "ocr":
            return model.read(image)
        if name == "slm":
            return model.predict(prompt)
        return model.complete(image, prompt, schema)

    return ready(name, device, call)
