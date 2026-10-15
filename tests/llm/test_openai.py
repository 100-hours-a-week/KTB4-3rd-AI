# luna 요청 본문과 재시도 규칙만 본다. 네트워크는 쓰지 않는다.
import httpx

from app.llm.providers import openai

OK = {"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"cost": 3000}'}]}]}


def fake_post(statuses, bodies):
    def post(url, headers, json, timeout):
        bodies.append(json)
        status = statuses.pop(0)
        return httpx.Response(status, json=OK if status == 200 else {}, request=httpx.Request("POST", url))
    return post


def run(monkeypatch, statuses, schema=None):
    bodies = []
    monkeypatch.setenv("OPENAI_API_KEY_1", "k")
    monkeypatch.setattr(openai.httpx, "post", fake_post(statuses, bodies))
    monkeypatch.setattr(openai, "RETRY_WAIT", 0)
    try:
        return openai.OpenAIEngine("1").complete(None, "p", schema), bodies
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
    monkeypatch.setattr(openai.httpx, "post", lambda url, headers, json, timeout: seen.append(headers) or httpx.Response(200, json=OK, request=httpx.Request("POST", url)))
    assert openai.OpenAIEngine("1").complete(None, "p") == '{"cost": 3000}'
    assert seen[0]["Authorization"] == "Bearer old"
    try:
        openai.OpenAIEngine("2").complete(None, "p")
    except Exception as exc:
        assert getattr(exc, "code", None) == "MODEL_NOT_READY"
    else:
        raise AssertionError("slot 2 must not use the legacy key")
