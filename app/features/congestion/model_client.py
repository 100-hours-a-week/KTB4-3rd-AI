"""혼잡도의 외부 모델 호출과 호출 정책을 한곳에서 처리한다."""

import asyncio
import os
import time
from math import isfinite
from typing import Any

import httpx

from ...core.exceptions import ModelNotReady, UpstreamError
from ...core.logging import get_logger, http_reason
from .config import (
    DECISIONS_ENDPOINT,
    DECISIONS_POLICY,
    JUDGE_ENDPOINT,
    JUDGE_POLICY,
    CallPolicy,
    ModelEndpoint,
)

logger = get_logger(__name__)

_JUDGE_SEMAPHORE = asyncio.Semaphore(JUDGE_POLICY.concurrency)
_DECISIONS_SEMAPHORE = asyncio.Semaphore(DECISIONS_POLICY.concurrency)


async def complete_json(prompt: str, *, transport: httpx.AsyncBaseTransport | None = None) -> str:
    body = {
        "model": JUDGE_ENDPOINT.model,
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": prompt}]}
        ],
        "text": {"format": {"type": "json_object"}},
    }
    async with _JUDGE_SEMAPHORE:
        data = await _call("responses", JUDGE_ENDPOINT, JUDGE_POLICY, body, transport)
    return str(data["output_text"])


async def decide(
    body: dict[str, Any], *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    async with _DECISIONS_SEMAPHORE:
        return await _call("decisions", DECISIONS_ENDPOINT, DECISIONS_POLICY, body, transport)


async def _call(
    api: str,
    endpoint: ModelEndpoint,
    policy: CallPolicy,
    body: dict[str, Any],
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    try:
        key = os.environ[endpoint.key_env]
        if not key:
            raise KeyError(endpoint.key_env)
    except KeyError as exc:
        logger.error(
            "congestion model key is missing: key=%s hint=%s",
            endpoint.key_env,
            endpoint.key_env,
        )
        raise ModelNotReady() from exc

    started = time.monotonic()
    response = None
    error = "-"
    reason = "not_json"
    try:
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {key}"},
            timeout=policy.timeout,
            transport=transport,
        ) as client:
            for attempt in range(1, policy.retries + 2):
                response = None
                error = "-"
                response = await client.post(endpoint.url, json=body)
                # 오류 메시지는 버리고 type/code만 공백 없이 기록한다.
                if response.is_error:
                    try:
                        detail = response.json()["error"]
                        error = "/".join(
                            "_".join(str(detail.get(field) or "-").split())
                            for field in ("type", "code")
                        )
                    except (ValueError, KeyError, TypeError, AttributeError):
                        pass
                if attempt <= policy.retries and (
                    response.status_code == 429 or 500 <= response.status_code < 600
                ):
                    delay = 1.0
                    if response.status_code == 429:
                        try:
                            seconds = float(response.headers.get("Retry-After", "1"))
                            if isfinite(seconds) and seconds >= 0:
                                delay = seconds
                        except ValueError:
                            pass
                        if delay > policy.timeout:
                            response.raise_for_status()
                    logger.warning(
                        "congestion model call retry: api=%s status=%s error=%s",
                        api,
                        response.status_code,
                        error,
                    )
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
                break
        assert response is not None
        data = response.json()
        if not isinstance(data, dict):
            raise TypeError("response must be an object")
        usage = data.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        if api == "responses":
            reason = "empty_output"
            text = "".join(
                part["text"]
                for item in data["output"]
                if item["type"] == "message"
                for part in item["content"]
                if part["type"] == "output_text"
            )
            if not text.strip():
                raise ValueError("output text is empty")
            data = {"output_text": text}
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, httpx.HTTPError):
            reason = http_reason(exc)
        logger.error(
            "congestion model call failed: api=%s reason=%s status=%s error=%s ms=%s hint=%s",
            api,
            reason,
            response.status_code if response is not None else "-",
            error,
            int((time.monotonic() - started) * 1000),
            endpoint.key_env,
        )
        raise UpstreamError() from exc
    logger.info(
        "congestion model call done: api=%s model=%s attempts=%s ms=%s tokens_in=%s tokens_out=%s",
        api,
        endpoint.model,
        attempt,
        int((time.monotonic() - started) * 1000),
        usage.get("input_tokens") if type(usage.get("input_tokens")) is int else "-",
        usage.get("output_tokens") if type(usage.get("output_tokens")) is int else "-",
    )
    return data
