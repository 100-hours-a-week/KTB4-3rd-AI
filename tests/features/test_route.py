# run_api는 바로 luna, run_gpu는 장치·결과에 따라 pipe 또는 luna로 간다. 모델과 다운로드는 가짜다.
import asyncio
import logging

import pytest

from app.core.exceptions import InternalError, ModelNotReady, UpstreamError
from app.features.receipt import main as receipt
from app.features.taxi_meter import main as taxi

IMAGE = object()
LUNA = {"cost": 1}
PIPE = {"cost": 2}


def setup(monkeypatch, module, device, outcome=None):
    calls = []

    async def load_image(url):
        return IMAGE

    async def model_api_run(image):
        assert image is IMAGE
        return LUNA

    async def gpu(*args):
        calls.append(args)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(module, "DEVICE", device)
    monkeypatch.setattr(module, "load_image", load_image)
    monkeypatch.setattr(module, "model_api_run", model_api_run)
    monkeypatch.setattr(module, "pipe", gpu)
    monkeypatch.setattr(module, "model_pipe_run", gpu)
    return calls


def run(module, caplog, fn):
    with caplog.at_level(logging.INFO, logger="app"):
        out = asyncio.run(getattr(module, fn)("http://img.test/a.png"))
    logs = [r.getMessage() for r in caplog.records if r.getMessage().startswith(("request route", "image model failed"))]
    return out, [line.split(" request_id=")[0] for line in logs]


@pytest.mark.parametrize("module", [taxi, receipt])
def test_run_api(monkeypatch, caplog, module):
    calls = setup(monkeypatch, module, "modal", PIPE)
    assert run(module, caplog, "run_api") == (LUNA, ["request route: route=luna reason=route_api"])
    assert calls == []


@pytest.mark.parametrize("module", [taxi, receipt])
@pytest.mark.parametrize("device, outcome, expected, logs", [
    ("off", PIPE, LUNA, ["request route: route=luna reason=device_off"]),
    ("modal", ModelNotReady(), LUNA, ["request route: route=luna reason=model_not_ready"]),
    ("modal", UpstreamError(), LUNA, ["image model failed: code=UPSTREAM_FAILED device=modal",
                                      "request route: route=luna reason=upstream_failed"]),
    ("cpu", InternalError(), LUNA, ["image model failed: code=INTERNAL device=cpu",
                                    "request route: route=luna reason=internal"]),
    ("cpu", None, LUNA, ["request route: route=luna reason=judge_fallback"]),
    ("modal", PIPE, PIPE, ["request route: route=pipe device=modal"]),
    ("cuda", PIPE, PIPE, ["request route: route=pipe device=cuda"]),
])
def test_run_gpu(monkeypatch, caplog, module, device, outcome, expected, logs):
    calls = setup(monkeypatch, module, device, outcome)
    assert run(module, caplog, "run_gpu") == (expected, logs)
    # Modal은 pipe(kind, image), 로컬은 model_pipe_run(image, device). off는 부르지 않는다.
    want = {"off": []}.get(device, [(module.KIND, IMAGE)] if device == "modal" else [(IMAGE, device)])
    assert calls == want
