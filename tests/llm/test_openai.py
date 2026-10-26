# luna 요청 본문과 재시도 규칙만 본다. 네트워크는 쓰지 않는다.
import asyncio
import types

import httpx

from app.llm.providers import openai

OK = {"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"cost": 3000}'}]}]}


def patch_post(monkeypatch, post):
    # httpx.AsyncClient.post를 바꿔치기한다. post(url, headers, json, timeout) -> httpx.Response
    async def fake(self, url, headers, json, timeout):
        return post(url, headers, json, timeout)

    monkeypatch.setattr(openai.httpx.AsyncClient, "post", fake)


def complete(slot, schema=None):
    return asyncio.run(openai.OpenAIEngine(slot).complete(None, "p", schema))


def fake_post(statuses, bodies):
    def post(url, headers, json, timeout):
        bodies.append(json)
        status = statuses.pop(0)
        return httpx.Response(status, json=OK if status == 200 else {}, request=httpx.Request("POST", url))
    return post


def run(monkeypatch, statuses, schema=None):
    bodies = []
    monkeypatch.setenv("OPENAI_API_KEY_1", "k")
    patch_post(monkeypatch, fake_post(statuses, bodies))
    monkeypatch.setattr(openai, "RETRY_WAIT", 0)
    try:
        return complete("1", schema), bodies
    except Exception as exc:
        return exc, bodies


def test_retry_once_on_429_and_5xx(monkeypatch):
    for first in (429, 503):
        text, bodies = run(monkeypatch, [first, 200])
        assert text == '{"cost": 3000}' and len(bodies) == 2


def test_no_retry_on_4xx_and_gives_up_after_two(monkeypatch):
    exc, bodies = run(monkeypatch, [400])
    assert getattr(exc, "code", None) == "UPSTREAM_FAILED" and len(bodies) == 1
    exc, bodies = run(monkeypatch, [500, 500])
    assert getattr(exc, "code", None) == "UPSTREAM_FAILED" and len(bodies) == 2


def test_retry_stays_within_timeout(monkeypatch):
    # 첫 503이 몇 초 뒤에 왔는지에 따라 재시도 여부와 재시도 대기 시간이 정해진다.
    def calls(spent):
        clock, timeouts = [0.0], []

        def post(url, headers, json, timeout):
            timeouts.append(timeout)
            clock[0] += spent
            return httpx.Response(503, json={}, request=httpx.Request("POST", url))

        # 이벤트 루프 시계는 건드리지 않도록 openai 모듈의 time만 바꾼다.
        monkeypatch.setattr(openai, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
        patch_post(monkeypatch, post)
        try:
            complete("1")
        except Exception:
            pass
        return timeouts

    monkeypatch.setenv("OPENAI_API_KEY_1", "k")
    monkeypatch.setattr(openai, "RETRY_WAIT", 0)
    assert calls(40) == [45]  # 남은 5초 < RETRY_MIN이라 재시도하지 않는다
    assert calls(20) == [45, 25]  # 재시도는 남은 시간만 기다린다


def test_retry_waits_without_blocking_loop(monkeypatch):
    # 재시도 대기는 asyncio.sleep이다. 그동안 다른 task가 돈다.
    monkeypatch.setenv("OPENAI_API_KEY_1", "k")
    monkeypatch.setattr(openai, "RETRY_WAIT", 0.05)
    patch_post(monkeypatch, fake_post([503, 200], []))
    ticks = []

    async def main():
        async def tick():
            for _ in range(3):
                ticks.append(1)
                await asyncio.sleep(0.01)

        text, _ = await asyncio.gather(openai.OpenAIEngine("1").complete(None, "p"), tick())
        return text

    assert asyncio.run(main()) == '{"cost": 3000}' and len(ticks) == 3


def test_schema_switches_format(monkeypatch):
    _, bodies = run(monkeypatch, [200])
    assert bodies[0]["text"]["format"] == {"type": "json_object"}
    schema = {"type": "object"}
    _, bodies = run(monkeypatch, [200], schema)
    assert bodies[0]["text"]["format"] == {"type": "json_schema", "name": "result", "schema": schema, "strict": True}


def test_legacy_key_only_for_slot_1(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY_1", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY_2", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "old")
    seen = []
    patch_post(monkeypatch, lambda url, headers, json, timeout: seen.append(headers) or httpx.Response(200, json=OK, request=httpx.Request("POST", url)))
    assert complete("1") == '{"cost": 3000}'
    assert seen[0]["Authorization"] == "Bearer old"
    try:
        complete("2")
    except Exception as exc:
        assert getattr(exc, "code", None) == "MODEL_NOT_READY"
    else:
        raise AssertionError("slot 2 must not use the legacy key")


def test_failure_log_has_reason_and_hint(monkeypatch, caplog):
    import logging

    body = {"error": {"type": "invalid_request_error", "code": "invalid_api_key", "message": "secret text"}}
    monkeypatch.setenv("OPENAI_API_KEY_1", "k")
    patch_post(monkeypatch, lambda url, headers, json, timeout: httpx.Response(
        401, json=body, headers={"x-request-id": "req_9"}, request=httpx.Request("POST", url)))
    with caplog.at_level(logging.INFO, logger="app"):
        try:
            complete("1")
        except Exception:
            pass
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("openai request failed")][0]
    assert "reason=http_4xx status=401 error=invalid_request_error/invalid_api_key openai_req=req_9" in line
    assert "hint=API 키가 틀렸거나 폐기됨" in line and "secret text" not in line
