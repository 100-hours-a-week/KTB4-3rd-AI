"""혼잡도 모델 호출의 요청 계약, 재시도와 안전한 로그를 확인한다."""

import json
import logging
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.exceptions import ModelNotReady, UpstreamError
from app.features.congestion import model_client


@pytest.fixture(autouse=True)
def model_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY_2", "secret-key")
    monkeypatch.setattr(model_client.asyncio, "sleep", AsyncMock())


@pytest.mark.asyncio
async def test_complete_json_returns_text_and_logs_usage(
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
        assert await model_client.complete_json(
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


@pytest.mark.asyncio
async def test_decide_retries_429_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0
    waits: list[float] = []
    monkeypatch.setattr(model_client.asyncio, "sleep", AsyncMock(side_effect=waits.append))

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
        assert await model_client.decide(
            {"model": "gpt-6-luna", "questions": []},
            transport=httpx.MockTransport(respond),
        ) == {"answers": []}
    assert calls == 2
    assert waits == [1.0]
    assert caplog.text.count("congestion model call retry:") == 1
    assert "attempts=2" in caplog.text
    assert "tokens_in=- tokens_out=-" in caplog.text


@pytest.mark.parametrize("failure", [503, 400, "timeout", "connect", "retry_timeout"])
@pytest.mark.asyncio
async def test_http_failure_obeys_retry_policy(
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
        await model_client.complete_json("비밀본문", transport=httpx.MockTransport(respond))
    assert calls == (2 if failure in {503, "retry_timeout"} else 1)
    if failure == "retry_timeout":
        assert "reason=timeout status=- error=-" in caplog.text
    assert caplog.text.count("congestion model call failed:") == 1
    assert "hint=OPENAI_API_KEY_2" in caplog.text
    assert "비밀본문" not in caplog.text
    assert "bad\n value" not in caplog.text


@pytest.mark.asyncio
async def test_missing_key_stops_before_network(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY_2")

    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail("키 없이 호출됨")

    with pytest.raises(ModelNotReady):
        await model_client.complete_json("prompt", transport=httpx.MockTransport(unexpected))
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
@pytest.mark.asyncio
async def test_missing_or_invalid_output_is_not_retried(
    body: Any, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=body)

    with pytest.raises(UpstreamError):
        await model_client.complete_json("prompt", transport=httpx.MockTransport(respond))
    assert calls == 1
    assert "reason=empty_output" in caplog.text
    assert "congestion model call done" not in caplog.text


@pytest.mark.parametrize("call", ["complete_json", "decide"])
@pytest.mark.parametrize("raw", ["not json", "[]"])
@pytest.mark.asyncio
async def test_invalid_response_json_is_upstream_error(call: str, raw: str) -> None:
    with pytest.raises(UpstreamError):
        await getattr(model_client, call)(
            "prompt" if call == "complete_json" else {},
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=raw)),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("api", ["responses", "decisions"])
async def test_api_semaphore_limits_calls_and_releases_after_cancellation(
    monkeypatch: pytest.MonkeyPatch, api: str,
) -> None:
    import asyncio

    policy = model_client.JUDGE_POLICY if api == "responses" else model_client.DECISIONS_POLICY
    name = "_JUDGE_SEMAPHORE" if api == "responses" else "_DECISIONS_SEMAPHORE"
    assert getattr(model_client, name)._value == policy.concurrency
    semaphore = asyncio.Semaphore(policy.concurrency)
    monkeypatch.setattr(model_client, name, semaphore)
    active = 0
    peak = 0
    entered = asyncio.Event()
    release = asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == policy.concurrency:
            entered.set()
        try:
            await release.wait()
            return httpx.Response(200, json={
                "answers": [],
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": "{}"}
                ]}],
            })
        finally:
            active -= 1

    transport = httpx.MockTransport(respond)
    call = model_client.complete_json if api == "responses" else model_client.decide
    argument = "prompt" if api == "responses" else {}
    tasks = [asyncio.create_task(call(argument, transport=transport))
             for _ in range(policy.concurrency * 2 + 1)]
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        # 다른 API는 한쪽의 호출 자리가 모두 차 있어도 진행해야 한다.
        other = model_client.decide if api == "responses" else model_client.complete_json
        other_argument = {} if api == "responses" else "prompt"
        response = {"answers": [], "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "{}"}
        ]}]}
        await asyncio.wait_for(other(
            other_argument, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
        ), timeout=1)
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks[1:]), timeout=1)
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert peak == policy.concurrency
    assert active == 0
    assert semaphore._value == policy.concurrency


@pytest.mark.asyncio
@pytest.mark.parametrize("api, timeout", [("responses", 60.0), ("decisions", 30.0)])
@pytest.mark.parametrize("header", ["2.5", "0", "equal", "long", "invalid", "-1", "nan", "inf"])
async def test_retry_after_seconds_obey_policy_timeout(
    monkeypatch: pytest.MonkeyPatch, api: str, timeout: float, header: str,
) -> None:
    calls = 0
    value = str(timeout) if header == "equal" else str(timeout + 1) if header == "long" else header
    sleep = AsyncMock()
    monkeypatch.setattr(model_client.asyncio, "sleep", sleep)

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": value})
        return httpx.Response(200, json={
            "answers": [], "output": [{"type": "message", "content": [
                {"type": "output_text", "text": "{}"}
            ]}],
        })

    call = model_client.complete_json if api == "responses" else model_client.decide
    argument = "prompt" if api == "responses" else {}
    if header == "long":
        with pytest.raises(UpstreamError) as failure:
            await call(argument, transport=httpx.MockTransport(respond))
        assert isinstance(failure.value.__cause__, httpx.HTTPStatusError)
        assert failure.value.__cause__.response.status_code == 429
        assert calls == 1
        sleep.assert_not_awaited()
    else:
        await call(argument, transport=httpx.MockTransport(respond))
        assert calls == 2
        delay = timeout if header == "equal" else float(header) if header in {"2.5", "0"} else 1.0
        sleep.assert_awaited_once_with(delay)
