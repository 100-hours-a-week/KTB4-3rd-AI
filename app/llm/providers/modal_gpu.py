# Modal T4 인터페이스. llm/modal_app.py로 배포한 OCR과 openjev를 원격으로 부른다.
# MODAL_TOKEN_ID, MODAL_TOKEN_SECRET은 modal 패키지가 환경변수에서 직접 읽는다.
import threading
from typing import Any

from ...core.exceptions import ModelNotReady, UpstreamError
from ...core.logging import get_logger, log_import_error
from ..types import ImageBytes

logger = get_logger(__name__)

APP = "oj-image-models"
CLS = "OjModels"
# 원격 호출 대기 상한(초). cold start로 넘기면 luna로 넘어가고, 원격 호출은 계속 돌아 컨테이너를 데운다.
WAIT = 30


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
                    log_import_error(logger, "modal", ("modal",), exc)
                    raise ModelNotReady() from exc
                try:
                    # hydrate로 토큰과 배포된 앱을 지금 확인한다. 없으면 기동 때 한 번만 실패한다.
                    self.remote = modal.Cls.from_name(APP, CLS).hydrate()()
                except Exception as exc:
                    # 토큰이 없거나 앱이 배포되지 않았으면 여기서 실패할 수 있다. 기능 폴더가 luna로 넘긴다.
                    logger.error("modal lookup failed: reason=%s", type(exc).__name__)
                    raise ModelNotReady() from exc
            return self.remote

    def read(self, image: ImageBytes) -> str:
        return self.call("read", image.content, image.media_type)

    def predict(self, pairs: list[tuple[str, str]]) -> list[list[float]]:
        return self.call("predict", pairs)

    def call(self, method: str, *args: bytes | str | list[tuple[str, str]]) -> str | list[list[float]]:
        remote = self.load()
        try:
            return getattr(remote, method).spawn(*args).get(timeout=WAIT)
        except Exception as exc:
            logger.error("modal call failed: method=%s reason=%s", method, type(exc).__name__)
            raise UpstreamError() from exc
