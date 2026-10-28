"""혼잡도 모델 호출의 요청 계약, 재시도와 안전한 로그를 확인한다."""

import json
import logging
from typing import Any

import httpx
import pytest

from app.core.exceptions import ModelNotReady, UpstreamError
from app.features.congestion import model_client


@pytest.fixture(autouse=True)
def model_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY_2", "secret-key")
    monkeypatch.setattr(model_client.time, "sleep", lambda seconds: None)


def test_complete_json_returns_text_and_logs_usage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert request.headers["Authorization"] == "Bearer secret-key"
        assert request.extensions["timeout"]["read"] == 60.0
        assert json.loads(request.content) == {
            "model": "gpt-6-luna",
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "비밀본문"}],
                }
            ],
            "text": {"format": {"type": "json_object"}},
        }
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"signals": []}'}],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    with caplog.at_level(logging.INFO):
        assert model_client.complete_json(
            "비밀본문", transport=httpx.MockTransport(respond)
        ) == ('{"signals": []}')
    assert caplog.text.count("congestion model call done:") == 1
    assert (
        "congestion model call done: api=responses model=gpt-6-luna attempts=1"
        in caplog.text
    )
    assert "tokens_in=10 tokens_out=5" in caplog.text
    assert "secret-key" not in caplog.text
    assert "비밀본문" not in caplog.text


def test_decide_retries_429_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0
    waits: list[float] = []
    monkeypatch.setattr(model_client.time, "sleep", waits.append)

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert str(request.url) == "https://api.openai.com/v1/decisions"
        assert request.extensions["timeout"]["read"] == 30.0
        assert json.loads(request.content) == {"model": "gpt-6-luna", "questions": []}
        if calls == 1:
            return httpx.Response(
                429, json={"error": {"type": "rate_limit", "code": "limit"}}
            )
        return httpx.Response(200, json={"answers": []})

    with caplog.at_level(logging.INFO):
        assert model_client.decide(
            {"model": "gpt-6-luna", "questions": []},
            transport=httpx.MockTransport(respond),
        ) == {"answers": []}
    assert calls == 2
    assert waits == [1.0]
    assert caplog.text.count("congestion model call retry:") == 1
    assert "attempts=2" in caplog.text
    assert "tokens_in=- tokens_out=-" in caplog.text


@pytest.mark.parametrize("failure", [503, 400, "timeout", "connect", "retry_timeout"])
def test_http_failure_obeys_retry_policy(
    failure: int | str, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failure == "retry_timeout" and calls == 1:
            return httpx.Response(429)
        if failure in {"timeout", "retry_timeout"}:
            raise httpx.ReadTimeout("비밀본문", request=request)
        if failure == "connect":
            raise httpx.ConnectError("비밀본문", request=request)
        return httpx.Response(
            int(failure),
            json={
                "error": {
                    "type": "server_error",
                    "code": "bad\n value",
                    "message": "비밀본문",
                }
            },
        )

    with pytest.raises(UpstreamError):
        model_client.complete_json("비밀본문", transport=httpx.MockTransport(respond))
    assert calls == (2 if failure in {503, "retry_timeout"} else 1)
    if failure == "retry_timeout":
        assert "reason=timeout status=- error=-" in caplog.text
    assert caplog.text.count("congestion model call failed:") == 1
    assert "hint=OPENAI_API_KEY_2" in caplog.text
    assert "비밀본문" not in caplog.text
    assert "bad\n value" not in caplog.text


def test_missing_key_stops_before_network(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY_2")

    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail("키 없이 호출됨")

    with pytest.raises(ModelNotReady):
        model_client.complete_json("prompt", transport=httpx.MockTransport(unexpected))
    assert "congestion model key is missing: key=OPENAI_API_KEY_2" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"output": []},
        {"output": None},
        {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": 1}]}
            ]
        },
        {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "  "}]}
            ]
        },
    ],
)
def test_missing_or_invalid_output_is_not_retried(
    body: Any, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=body)

    with pytest.raises(UpstreamError):
        model_client.complete_json("prompt", transport=httpx.MockTransport(respond))
    assert calls == 1
    assert "reason=empty_output" in caplog.text
    assert "congestion model call done" not in caplog.text


@pytest.mark.parametrize("call", ["complete_json", "decide"])
@pytest.mark.parametrize("raw", ["not json", "[]"])
def test_invalid_response_json_is_upstream_error(call: str, raw: str) -> None:
    with pytest.raises(UpstreamError):
        getattr(model_client, call)(
            "prompt" if call == "complete_json" else {},
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=raw)),
        )
