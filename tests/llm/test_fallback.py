# 준비 실패한 로컬 장치는 한 번만 시도하고, Modal 대기 상한을 넘기면 luna로 넘길 수 있는 오류가 나는지 본다.
import asyncio
import threading
import types

from app.core.exceptions import ModelNotReady, UpstreamError
from app.llm import router
from app.llm.providers.modal_gpu import WAIT, ModalEngine
from app.llm.types import ImageBytes

IMAGE = ImageBytes(b"x", 1, 1, "image/png", "test")


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
            asyncio.run(router.llm("ocr", device="cpu"))
        except ModelNotReady:
            pass
    assert Broken.calls == 1 and ("ocr", "cpu") in router.down


def test_llm_runs_local_in_thread_and_awaits_api(monkeypatch):
    seen = {}

    class Local:
        def predict(self, pairs):
            seen["thread"] = threading.current_thread() is not threading.main_thread()
            return [[0.1, 0.8, 0.1]]

    class Api:
        async def complete(self, image, prompt, schema):
            seen["api"] = (image, prompt, schema)
            return "{}"

    monkeypatch.setattr(router, "down", {("api_1", "cpu")})  # api는 down을 보지 않는다
    monkeypatch.setattr(router, "engine", lambda name, device: Api() if name == "api_1" else Local())
    assert asyncio.run(router.llm("slm", [("a", "b")], device="cpu")) == [[0.1, 0.8, 0.1]] and seen["thread"]
    assert asyncio.run(router.llm("api_1", "p", IMAGE, schema={"s": 1})) == "{}"
    assert seen["api"] == (IMAGE, "p", {"s": 1})


def test_modal_device_has_no_engine_path():
    # Modal은 llm이 아니라 pipe로만 부른다. ("ocr","modal")로 Modal 엔진을 만들지 않는다.
    assert not isinstance(router.engine("ocr", "modal"), ModalEngine)
    router.engines.pop(("ocr", "modal"), None)


def test_modal_wait_limit_raises_upstream():
    waited = []

    async def get(timeout):
        waited.append(timeout)
        raise TimeoutError()

    async def spawn(*args):
        return types.SimpleNamespace(get=types.SimpleNamespace(aio=get))

    engine = ModalEngine()
    engine.state = "ready"
    engine.remote = types.SimpleNamespace(pipe=types.SimpleNamespace(spawn=types.SimpleNamespace(aio=spawn)))
    try:
        asyncio.run(engine.pipe("taxi_meter", IMAGE))
    except UpstreamError:
        assert waited == [WAIT]
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
