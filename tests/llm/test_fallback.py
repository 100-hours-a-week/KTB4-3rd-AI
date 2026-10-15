# 준비 실패 장치는 한 번만 시도하고, Modal 대기 상한을 넘기면 luna로 넘길 수 있는 오류가 나는지 본다.
from app.core.exceptions import ModelNotReady, UpstreamError
from app.llm import router
from app.llm.providers.modal_gpu import WAIT, ModalEngine


class Broken:
    calls = 0

    def read(self, image):
        Broken.calls += 1
        raise ModelNotReady()


def test_down_device_fails_fast(monkeypatch):
    monkeypatch.setattr(router, "down", set())
    monkeypatch.setattr(router, "engine", lambda name, device: Broken())
    for _ in range(3):
        try:
            router.llm("ocr", device="modal")
        except ModelNotReady:
            pass
    assert Broken.calls == 1 and ("ocr", "modal") in router.down


class Late:
    waited = None

    def get(self, timeout):
        Late.waited = timeout
        raise TimeoutError()


class Remote:
    class read:
        @staticmethod
        def spawn(*args):
            return Late()


def test_modal_wait_limit_raises_upstream():
    engine = ModalEngine()
    engine.remote = Remote()
    try:
        engine.call("read", b"", "image/png")
    except UpstreamError:
        assert Late.waited == WAIT
        return
    raise AssertionError("expected UpstreamError")


def test_import_error_levels(caplog):
    import logging

    from app.core.logging import log_import_error

    log = logging.getLogger("app.test")
    with caplog.at_level(logging.INFO, logger="app.test"):
        log_import_error(log, "ocr", ("paddleocr",), ModuleNotFoundError("x", name="paddleocr"))
        log_import_error(log, "ocr", ("paddleocr",), ModuleNotFoundError("x", name="cv2"))
        log_import_error(log, "ocr", ("paddleocr",), ImportError("libGL.so.1"))
    levels = [(r.levelname, r.exc_info is not None) for r in caplog.records]
    assert levels == [("WARNING", False), ("ERROR", True), ("ERROR", True)]
