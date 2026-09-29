# llm(모델명, 프롬프트, 이미지, device)은 모델을 한 번 돌리고 결과만 돌려준다.
# - ocr:    image → 읽은 원문 str
# - slm:    prompt=[(원문, 가설), ...] → 가설마다 [모순, 지지, 중립]
# - api:    prompt, image → JSON 문자열
# device가 "modal"이면 ocr과 slm은 Modal T4에서 돈다. 반환 형식은 같다.
# 순서 제어와 파이프라인 구성은 기능 폴더가 맡는다.
import threading

from ..core.exceptions import InternalError
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


def engine(name: ModelName, device: str) -> Engine:
    key = (name, device)
    with lock:
        if key not in engines:
            if name == "api":
                engines[key] = OpenAIEngine()
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


def load(name: ModelName, device: str = "cpu") -> None:
    engine(name, device).load()


def llm(
    name: ModelName,
    prompt: str | list[tuple[str, str]] | None = None,
    image: ImageBytes | None = None,
    device: str = "cpu",
) -> str | list[list[float]]:
    model = engine(name, device)
    if name == "ocr":
        return model.read(image)
    if name == "slm":
        return model.predict(prompt)
    return model.complete(image, prompt)
