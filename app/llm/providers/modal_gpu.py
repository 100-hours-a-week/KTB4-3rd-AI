# Modal T4 인터페이스. llm/modal_app.py로 배포한 OCR과 openjev를 원격으로 부른다.
# MODAL_TOKEN_ID, MODAL_TOKEN_SECRET은 modal 패키지가 환경변수에서 직접 읽는다.
import threading
from typing import Any

from ...core.exceptions import ModelNotReady, UpstreamError
from ...core.logging import get_logger
from ..types import ImageBytes

logger = get_logger(__name__)

APP = "oj-image-models"
CLS = "OjModels"


class ModalEngine:
    def __init__(self) -> None:
        self.remote = None
        self.lock = threading.Lock()

    def load(self) -> Any:
        with self.lock:
            if self.remote is None:
                try:
                    import modal
                except ImportError as exc:
                    logger.exception("modal is not installed")
                    raise ModelNotReady("modal is not ready") from exc
                self.remote = modal.Cls.from_name(APP, CLS)()
            return self.remote

    def read(self, image: ImageBytes) -> str:
        return self.call("read", image.content, image.media_type)

    def predict(self, pairs: list[tuple[str, str]]) -> list[list[float]]:
        return self.call("predict", pairs)

    def call(self, method: str, *args: bytes | str | list[tuple[str, str]]) -> str | list[list[float]]:
        remote = self.load()
        try:
            return getattr(remote, method).remote(*args)
        except Exception as exc:
            logger.error("modal call failed: method=%s reason=%s", method, type(exc).__name__)
            raise UpstreamError() from exc
