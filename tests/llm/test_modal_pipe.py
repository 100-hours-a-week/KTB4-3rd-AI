# Modal 자동 준비(조회 → 배포 → ready)와 pipe 호출 규칙을 가짜 modal로 본다. Modal 서버는 부르지 않는다.
import asyncio
import importlib
import logging
import sys
import time
import types

import app.llm as llm_pkg
from app.core.exceptions import ModelNotReady, UpstreamError
from app.llm import router
from app.llm.providers import modal_gpu
from app.llm.providers.modal_gpu import APP, APP_BASE, VERSION, WAIT, ModalEngine
from app.llm.types import ImageBytes

IMAGE = ImageBytes(b"png", 1, 1, "image/png", "test")


class NotFoundError(Exception):
    pass


class AuthError(Exception):
    pass


def fake_modal(monkeypatch, seen, found, lookup_error=None, deploy_error=None, gate=None, credentials_error=None):
    # found: 조회 때마다 하나씩 꺼낸다. False면 NotFoundError.
    # 진짜 API가 비동기인 것(from_credentials, hydrate, deploy)은 .aio만 둔다. 동기로 부르면 테스트가 실패한다.
    async def credentials(token_id, token_secret):
        seen["credentials"] = (token_id, token_secret)
        if credentials_error:
            raise credentials_error
        return "client"

    class Client:
        from_credentials = types.SimpleNamespace(aio=credentials)

    class Cls:
        def __init__(self):
            self.hydrate = types.SimpleNamespace(aio=self.check)

        @staticmethod
        def from_name(app, name, client=None):
            seen.setdefault("lookups", []).append((app, name, client))
            return Cls()

        async def check(self, client):
            if lookup_error:
                raise lookup_error
            if not found.pop(0):
                raise NotFoundError()

        def __call__(self):
            return "remote"

    async def deploy(name, client):
        seen.setdefault("deploys", []).append((name, client))
        if gate:
            await gate.wait()
        if deploy_error:
            raise deploy_error

    modal = types.SimpleNamespace(Client=Client, Cls=Cls, exception=types.SimpleNamespace(NotFoundError=NotFoundError))
    app_module = types.SimpleNamespace(app=types.SimpleNamespace(deploy=types.SimpleNamespace(aio=deploy)))
    monkeypatch.setitem(sys.modules, "modal", modal)
    # from .. import modal_app은 패키지 속성을 먼저 본다. 진짜 modal_app을 불러온 적이 있어도 가짜를 쓰게 한다.
    monkeypatch.setitem(sys.modules, "app.llm.modal_app", app_module)
    monkeypatch.setattr(llm_pkg, "modal_app", app_module, raising=False)
    monkeypatch.setenv("MODAL_TOKEN_ID_1", "id-1")
    monkeypatch.setenv("MODAL_TOKEN_SECRET_1", "secret-1")
    monkeypatch.setenv("MODAL_TOKEN_ID_2", "id-2")


def messages(caplog, prefix):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(prefix)]


def test_version_follows_content(tmp_path):
    def tree(root, order):
        for rel, text in order:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return modal_gpu.version(root)

    files = [("llm/a.py", "a"), ("core/b.py", "b"), ("features/taxi_meter/c.py", "c"), ("features/receipt/d.py", "d")]
    first = tree(tmp_path / "one", files)
    assert len(first) == 12 and int(first, 16) >= 0
    assert tree(tmp_path / "two", files[::-1]) == first  # 만든 순서와 무관
    assert tree(tmp_path / "two", [("features/congestion/e.py", "e"), ("llm/a.txt", "x")]) == first  # 범위 밖 파일은 무관
    assert tree(tmp_path / "two", [("core/b.py", "b2")]) != first  # 내용이 바뀌면 바뀐다
    assert VERSION == modal_gpu.version() and APP == f"{APP_BASE}-{VERSION}"


def test_prepare_uses_existing_app(monkeypatch):
    seen = {}
    fake_modal(monkeypatch, seen, found=[True])
    engine = ModalEngine("1")
    asyncio.run(engine.prepare())
    assert engine.state == "ready" and engine.remote == "remote" and "deploys" not in seen
    assert seen["credentials"] == ("id-1", "secret-1") and seen["lookups"] == [(APP, "OjModels", "client")]


def test_prepare_deploys_once_when_missing(monkeypatch, caplog):
    seen = {}
    fake_modal(monkeypatch, seen, found=[False, True])
    engine = ModalEngine("1")
    with caplog.at_level(logging.INFO, logger="app"):
        asyncio.run(engine.prepare())
    assert engine.state == "ready" and seen["deploys"] == [(APP, "client")] and len(seen["lookups"]) == 2
    assert messages(caplog, "modal deploy started") == [f"modal deploy started: app={APP}"]
    assert messages(caplog, f"modal deploy done: app={APP} ms=")
    assert messages(caplog, "modal state changed") == [
        "modal state changed: from=idle to=deploying", "modal state changed: from=deploying to=ready"]


def test_prepare_token_error_marks_down(monkeypatch, caplog):
    seen = {}
    fake_modal(monkeypatch, seen, found=[], lookup_error=AuthError())
    engine = ModalEngine("1")
    with caplog.at_level(logging.INFO, logger="app"):
        asyncio.run(engine.prepare())
    assert engine.state == "down" and "deploys" not in seen
    assert messages(caplog, "modal lookup failed: reason=AuthError slot=1 hint=")


def test_bad_credentials_marks_down(monkeypatch, caplog):
    seen = {}
    fake_modal(monkeypatch, seen, found=[], credentials_error=AuthError())
    engine = ModalEngine("1")
    with caplog.at_level(logging.INFO, logger="app"):
        asyncio.run(engine.prepare())
    assert engine.state == "down" and "lookups" not in seen and "deploys" not in seen
    assert messages(caplog, "modal lookup failed: reason=AuthError slot=1 hint=")


def test_deploy_failure_marks_down(monkeypatch, caplog):
    seen = {}
    fake_modal(monkeypatch, seen, found=[False], deploy_error=RuntimeError("build"))
    engine = ModalEngine("1")
    with caplog.at_level(logging.INFO, logger="app"):
        asyncio.run(engine.prepare())
    assert engine.state == "down" and len(seen["deploys"]) == 1
    assert messages(caplog, f"modal deploy failed: app={APP} reason=RuntimeError hint=")


def test_missing_modal_package_marks_down(monkeypatch):
    monkeypatch.setitem(sys.modules, "modal", None)
    engine = ModalEngine("1")
    asyncio.run(engine.prepare())
    assert engine.state == "down"


def test_pipe_rejects_at_once_while_deploying(monkeypatch, caplog):
    seen = {}

    async def main():
        gate = asyncio.Event()
        fake_modal(monkeypatch, seen, found=[False, True], gate=gate)
        engine = ModalEngine("1")
        task = asyncio.create_task(engine.prepare())
        while engine.state != "deploying":
            await asyncio.sleep(0)
        started = time.monotonic()
        for _ in range(3):
            try:
                await engine.pipe("taxi_meter", IMAGE)
            except ModelNotReady:
                pass
            else:
                raise AssertionError("expected ModelNotReady")
        elapsed = time.monotonic() - started
        gate.set()
        await task
        engine.set("ready")  # 같은 상태는 로그를 남기지 않는다
        return engine, elapsed

    with caplog.at_level(logging.INFO, logger="app"):
        engine, elapsed = asyncio.run(main())
    assert elapsed < 0.05 and engine.state == "ready"
    assert len(messages(caplog, "modal state changed")) == 2


def ready_engine(spawn_error=None, get_error=None, result=None):
    seen = {}

    async def get(timeout):
        seen["timeout"] = timeout
        if get_error:
            raise get_error
        return result

    async def spawn(*args):
        seen["args"] = args
        if spawn_error:
            raise spawn_error
        return types.SimpleNamespace(get=types.SimpleNamespace(aio=get))

    engine = ModalEngine("1")
    engine.state = "ready"
    engine.remote = types.SimpleNamespace(pipe=types.SimpleNamespace(spawn=types.SimpleNamespace(aio=spawn)))
    return engine, seen


def test_pipe_spawns_once_and_waits_limit():
    engine, seen = ready_engine(result={"cost": 3000})
    assert asyncio.run(engine.pipe("receipt", IMAGE)) == {"cost": 3000}
    assert seen == {"args": ("receipt", b"png", "image/png"), "timeout": WAIT} and WAIT == 15


def test_pipe_failure_reasons(caplog):
    cases = {
        "TimeoutError": "timeout", "FunctionTimeoutError": "timeout", "AuthError": "auth", "NotFoundError": "not_found",
        "ResourceExhaustedError": "quota", "PermissionDeniedError": "permission", "ValueError": "remote_error",
    }
    for name, reason in cases.items():
        error = type(name, (Exception,), {})()
        for engine, _ in (ready_engine(get_error=error), ready_engine(spawn_error=error)):
            caplog.clear()
            with caplog.at_level(logging.INFO, logger="app"):
                try:
                    asyncio.run(engine.pipe("taxi_meter", IMAGE))
                except UpstreamError:
                    pass
                else:
                    raise AssertionError("expected UpstreamError")
            assert messages(caplog, f"modal call failed: method=pipe reason={reason} error={name} ms=")
            assert engine.state == "ready"  # 실패해도 다음 작업은 다시 시도한다


def test_router_exports_pipe_and_prepare(monkeypatch):
    from app.llm import pipe, prepare_modal

    calls = []

    class Fake:
        async def pipe(self, kind, image):
            calls.append(("pipe", kind))
            return {"cost": 1}

        async def prepare(self):
            calls.append(("prepare",))

    monkeypatch.setattr(router, "MODAL", Fake())
    assert asyncio.run(pipe("taxi_meter", IMAGE)) == {"cost": 1}
    asyncio.run(prepare_modal())
    assert calls == [("pipe", "taxi_meter"), ("prepare",)]


def test_slm_load_place_then_move(monkeypatch):
    from app.llm.providers import slm

    moves = []

    class Model:
        config = types.SimpleNamespace(get_text_config=lambda: types.SimpleNamespace(pad_token_id=1))

        def to(self, device):
            moves.append(device)
            return self

        def eval(self):
            return self

    class Auto:
        @staticmethod
        def from_pretrained(repo, subfolder, dtype):
            moves.append(dtype)
            return Model()

    class Tokenizer:
        @staticmethod
        def from_pretrained(repo, subfolder):
            return types.SimpleNamespace(pad_token="p", eos_token="e", pad_token_id=1)

    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(float16="fp16", float32="fp32"))
    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(
        AutoModelForSequenceClassification=Auto, AutoTokenizer=Tokenizer))
    engine = slm.SlmEngine("cuda")
    engine.load(place="cpu")
    engine.to("cuda")
    assert moves == ["fp16", "cpu", "cuda"]  # dtype은 최종 장치(cuda) 기준
    local = slm.SlmEngine("mps")
    local.load()
    assert moves[-2:] == ["fp32", "mps"]


def test_modal_app_settings(monkeypatch):
    # 설치된 modal로 불러오기만 한다(배포하지 않는다). 클래스 설정값과 enter·pipe 동작을 본다.
    import modal

    captured = {}
    original = modal.App.cls

    def cls(self, *args, **kwargs):
        captured.update(kwargs)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(modal.App, "cls", cls)
    monkeypatch.delitem(sys.modules, "app.llm.modal_app", raising=False)
    monkeypatch.setattr(llm_pkg, "modal_app", None, raising=False)
    module = importlib.import_module("app.llm.modal_app")
    assert module.app.name == APP and module.GPU_SNAPSHOT is False
    assert {k: v for k, v in captured.items() if k != "volumes"} == {
        "gpu": "T4", "memory": 16384, "min_containers": 0, "max_containers": 1, "scaledown_window": 60,
        "timeout": 120, "enable_memory_snapshot": True, "experimental_options": None}
    assert list(captured["volumes"]) == ["/cache"]

    calls = []

    class Slm:
        def __init__(self, device):
            calls.append(("slm", device))

        def load(self, place=None):
            calls.append(("load", place))

        def to(self, device):
            calls.append(("to", device))

    class Ocr:
        def __init__(self, device):
            calls.append(("ocr", device))

        def load(self):
            calls.append(("ocr.load",))

    async def run(image, device):
        calls.append(("run", image, device))
        return {"cost": 2}

    monkeypatch.setattr(module, "SlmEngine", Slm)
    monkeypatch.setattr(module, "OcrEngine", Ocr)
    monkeypatch.setattr(module, "cache", types.SimpleNamespace(commit=lambda: calls.append(("commit",))))
    monkeypatch.setitem(module.PIPES, "taxi_meter", run)
    monkeypatch.setattr(router, "engines", {})
    user = module.OjModels._get_user_cls()
    obj = user.__new__(user)
    user.__dict__["load"]._get_raw_f()(obj)
    user.__dict__["start"]._get_raw_f()(obj)
    result = asyncio.run(user.__dict__["pipe"]._get_raw_f()(obj, "taxi_meter", b"png", "image/png"))
    assert calls[:7] == [("slm", "cuda"), ("load", "cpu"), ("commit",), ("to", "cuda"), ("ocr", "cuda"), ("ocr.load",), ("commit",)]
    assert calls[7] == ("run", ImageBytes(b"png", 0, 0, "image/png", ""), "cuda") and result == {"cost": 2}
    assert set(router.engines) == {("slm", "cuda"), ("ocr", "cuda")}
