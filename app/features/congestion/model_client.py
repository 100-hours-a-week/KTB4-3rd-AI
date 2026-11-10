"""혼잡도의 외부 모델 호출과 호출 정책을 한곳에서 처리한다."""

import asyncio
import os
import time
from contextvars import ContextVar
from math import isfinite
from typing import Any

import httpx

from ...core.exceptions import ModelNotReady, UpstreamError
from ...core.logging import get_logger, http_reason
from .config import (
    DECISIONS_ENDPOINT,
    DECISIONS_POLICY,
    JUDGE_ENDPOINTS,
    JUDGE_MAX_TOKENS,
    JUDGE_POLICY,
    CallPolicy,
    ModelEndpoint,
)
from .extractor import BundleDecision

logger = get_logger(__name__)
judge_model: ContextVar[str] = ContextVar("judge_model", default="-")


def _judge_schema(node: Any) -> Any:
    """Pydantic 스키마에서 두 회사가 함께 지원하는 제약만 남긴다."""
    if isinstance(node, list):
        return [_judge_schema(value) for value in node]
    if not isinstance(node, dict):
        return node
    result = {key: _judge_schema(value) for key, value in node.items() if key not in {
        "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum",
        "exclusiveMaximum", "minItems", "maxItems", "multipleOf", "pattern", "format",
    }}
    if result.get("type") == "object":
        result["additionalProperties"] = False
        result["required"] = list(result["properties"])
    return result


JUDGE_OUTPUT_SCHEMA = _judge_schema(BundleDecision.model_json_schema())

_JUDGE_SEMAPHORE = asyncio.Semaphore(JUDGE_POLICY.concurrency)
_DECISIONS_SEMAPHORE = asyncio.Semaphore(DECISIONS_POLICY.concurrency)


def _headers(endpoint: ModelEndpoint, key: str) -> dict[str, str]:
    if endpoint.provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {key}"}


def _judge_body(endpoint: ModelEndpoint, prompt: str) -> dict[str, Any]:
    if endpoint.provider == "anthropic":
        return {
            "model": endpoint.model, "max_tokens": JUDGE_MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"format": {"type": "json_schema", "schema": JUDGE_OUTPUT_SCHEMA}},
        }
    return {
        "model": endpoint.model,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "text": {"format": {"type": "json_schema", "name": "congestion_judgement",
                            "schema": JUDGE_OUTPUT_SCHEMA, "strict": True}},
    }


def _judge_text(endpoint: ModelEndpoint, data: dict[str, Any]) -> str:
    if endpoint.provider == "anthropic":
        if data.get("stop_reason") in {"refusal", "max_tokens"}:
            raise ValueError("판정 응답이 거부되거나 잘림")
        text = "".join(part["text"] for part in data["content"] if part["type"] == "text")
    else:
        text = "".join(
            part["text"] for item in data["output"] if item["type"] == "message"
            for part in item["content"] if part["type"] == "output_text"
        )
    if not text.strip():
        raise ValueError("판정 응답이 비어 있음")
    return text


async def complete_json(prompt: str, *, transport: httpx.AsyncBaseTransport | None = None) -> str:
    judge_model.set("-")
    failure: ModelNotReady | UpstreamError = ModelNotReady()
    async with _JUDGE_SEMAPHORE:
        for index, endpoint in enumerate(JUDGE_ENDPOINTS):
            try:
                data = await _call(
                    "responses", endpoint, JUDGE_POLICY, _judge_body(endpoint, prompt), transport
                )
            except (ModelNotReady, UpstreamError) as exc:
                if isinstance(exc, UpstreamError):
                    failure = exc
                cause = exc.__cause__
                if isinstance(cause, httpx.HTTPStatusError):
                    status = cause.response.status_code
                    if 400 <= status < 500 and status != 429:
                        raise
                reason = ("missing_key" if isinstance(exc, ModelNotReady) else
                          http_reason(cause) if isinstance(cause, httpx.HTTPError) else "not_json")
                if index + 1 < len(JUDGE_ENDPOINTS):
                    logger.warning(
                        "congestion model fallback: api=responses from=%s to=%s reason=%s",
                        endpoint.model, JUDGE_ENDPOINTS[index + 1].model, reason,
                    )
            else:
                judge_model.set(endpoint.model)
                return str(data["output_text"])
    raise failure


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
        logger.warning(
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
            headers=_headers(endpoint, key),
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
            data = {"output_text": _judge_text(endpoint, data)}
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, httpx.HTTPError):
            reason = http_reason(exc)
        logger.warning(
            "congestion model call failed: api=%s model=%s "
            "reason=%s status=%s error=%s ms=%s hint=%s",
            api,
            endpoint.model,
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
