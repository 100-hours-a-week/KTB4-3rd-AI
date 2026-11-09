# 헬스체크, request_id, 실패 로그의 원인·안내가 바로 읽히는지 본다. 네트워크는 쓰지 않는다.
import asyncio
import logging

import httpx
from fastapi.testclient import TestClient

from app.core.exceptions import JudgeParseError
from app.core.logging import http_reason
from app.features.receipt import pipeline as receipt
from app.features.taxi_meter import pipeline as taxi
from app.main import app


def test_health_and_request_id(caplog):
    with TestClient(app) as client, caplog.at_level(logging.INFO, logger="app"):
        assert client.get("/health").json() == {"status": "ok"}
        res = client.post("/taxi_meter", json={}, headers={"X-Request-ID": "spring-123"})
    assert res.status_code == 422 and res.headers["X-Request-ID"] == "spring-123"
    failed = [r.getMessage() for r in caplog.records if r.getMessage().startswith("request failed")]
    assert failed == ["request failed: path=/taxi_meter status=422 code=REQUEST_INVALID "
                      "hint=요청 JSON에 문자열 image_URL이 있는지 확인 request_id=spring-123"]


def test_http_reason():
    req = httpx.Request("GET", "http://x")
    status = lambda code: httpx.HTTPStatusError("e", request=req, response=httpx.Response(code, request=req))
    assert http_reason(httpx.ConnectTimeout("t")) == "timeout"
    assert http_reason(httpx.ConnectError("c")) == "connect"
    assert http_reason(status(404)) == "http_4xx"
    assert http_reason(status(503)) == "http_5xx"
    assert http_reason(httpx.UnsupportedProtocol("u")) == "bad_url"


def parse_reason(monkeypatch, caplog, module, raw):
    async def llm(*a, **k):
        return raw

    monkeypatch.setattr(module, "llm", llm)
    with caplog.at_level(logging.INFO, logger="app"):
        try:
            asyncio.run(module.model_api_run(None))
        except JudgeParseError:
            return [r.getMessage() for r in caplog.records if "model output is not valid" in r.getMessage()][-1]
    raise AssertionError("expected JudgeParseError")


def test_parse_reasons(monkeypatch, caplog):
    assert parse_reason(monkeypatch, caplog, taxi, '{"cost": null}').endswith("field=cost reason=unreadable")
    assert parse_reason(monkeypatch, caplog, taxi, '{"cost": "약 3천원"}').endswith("field=cost reason=not_amount")
    done = '{"amount_cost": 1000, "transfer_info": "예약 이체", "sender_name": "a", "send_time": "b"}'
    assert parse_reason(monkeypatch, caplog, receipt, done).endswith("field=transfer_info reason=transfer_not_done")
    nulls = '{"amount_cost": null, "transfer_info": "이체 완료", "sender_name": null, "send_time": "b"}'
    assert parse_reason(monkeypatch, caplog, receipt, nulls).endswith("field=amount_cost,sender_name reason=unreadable")
