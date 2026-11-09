# 본체 연결: 동시성 상한 대기도 45초 deadline 안, unhandled 로그의 request_id, LLM 호출 기록 사유.
import asyncio
import logging
import time

import httpx
from fastapi.testclient import TestClient

from app.Server_Monitor_Bot.hooks import records, state
from app.llm import router
from app.llm.providers import openai
from app.main import app


def test_concurrency_wait_stays_inside_deadline(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY_1", "k")
    monkeypatch.setattr(openai, "TIMEOUT", 0.4)
    monkeypatch.setattr(openai, "RETRY_MIN", 0.1)
    held = asyncio.Semaphore(1)

    async def run():
        await held.acquire()
        monkeypatch.setattr(openai, "semaphore", lambda: held)
        started = time.monotonic()
        try:
            await openai.OpenAIEngine("1").complete(None, "p")
        except Exception as exc:
            return exc, time.monotonic() - started
        return None, time.monotonic() - started

    exc, spent = asyncio.run(run())
    assert getattr(exc, "code", None) == "UPSTREAM_FAILED" and spent < 0.4


def test_post_uses_shared_deadline(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY_1", "k")
    seen = []

    async def fake(self, url, headers, json, timeout):
        seen.append(timeout)
        return httpx.Response(200, json={"output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(openai.httpx.AsyncClient, "post", fake)
    monkeypatch.setattr(openai, "semaphore", lambda: asyncio.Semaphore(1))
    asyncio.run(openai.OpenAIEngine("1").complete(None, "p"))
    assert seen and seen[0] <= openai.TIMEOUT


def test_unhandled_log_has_request_id(caplog):
    async def boom():
        raise ValueError("x")

    app.add_api_route("/boom-test", boom)
    try:
        with TestClient(app, raise_server_exceptions=False) as client, caplog.at_level(logging.INFO, logger="app"):
            client.get("/boom-test", headers={"X-Request-ID": "boom-1"})
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/boom-test"]
    rec = [r for r in caplog.records if r.getMessage().startswith("unhandled error")][0]
    assert rec.request_id == "boom-1"


def test_llm_call_reason_prefers_detail(monkeypatch):
    rows = []
    monkeypatch.setattr(state, "INSTALLED", True)
    monkeypatch.setattr(state.QUEUE, "put_nowait", lambda item: rows.append(item))

    class Api:
        model = "m"

        async def complete(self, image, prompt, schema):
            records.last_call.set({"reason": "timeout", "status": None})
            raise openai.UpstreamError()

    monkeypatch.setattr(router, "engine", lambda name, device: Api())
    try:
        asyncio.run(router.llm("api_1", "p"))
    except Exception:
        pass
    assert rows[0][1]["reason"] == "timeout" and rows[0][1]["result"] == "fail"
