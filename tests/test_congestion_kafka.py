"""혼잡도 Kafka 요청을 읽어 분석하고 결과·DLT 메시지를 만드는지 검증한다."""

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from aiokafka import ConsumerRecord

from app.features.congestion.kafka_consumer import (
    ANALYSIS_COMPLETED,
    REQUEST_TOPIC,
    RESULT_TOPIC,
    CongestionRequestHandler,
)
from app.features.congestion.models import CongestionAnalysisMessage, CongestionResult
from app.features.congestion.schema_examples import REQUEST_EXAMPLE
from app.features.congestion.service import FakeCongestionAnalyzer
from app.kafka.envelope import InvalidMessage

WINDOW_END = datetime.fromisoformat(str(REQUEST_EXAMPLE["window_end"]))


class FailingAnalyzer:
    async def analyze(self, message: CongestionAnalysisMessage) -> CongestionResult:
        raise RuntimeError("boom")


class SlowAnalyzer:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def analyze(self, message: CongestionAnalysisMessage) -> CongestionResult:
        self.started.set()
        await asyncio.sleep(1)
        raise AssertionError("시간 제한을 넘기면 결과를 기다리지 않는다")


class CountingAnalyzer(FakeCongestionAnalyzer):
    calls = 0

    async def analyze(self, message: CongestionAnalysisMessage) -> CongestionResult:
        self.calls += 1
        return await super().analyze(message)


def request_data() -> dict[str, Any]:
    return {key: value for key, value in REQUEST_EXAMPLE.items() if key not in ("event_id", "schema_version")}


def record(
    *,
    event_type: str = "congestion.analysis.requested",
    schema_version: int = 1,
    data: dict[str, Any] | None = None,
    headers: tuple[tuple[str, bytes], ...] = (),
    value: bytes | None = None,
) -> ConsumerRecord:
    if value is None:
        value = json.dumps(
            {
                "event_id": "e6fad25e-bd51-4f6c-812f-33e01e2a8e34",
                "event_type": event_type,
                "schema_version": schema_version,
                "occurred_at": "2026-10-13T18:00:10+09:00",
                "data": request_data() if data is None else data,
            },
            ensure_ascii=False,
        ).encode()
    return ConsumerRecord(REQUEST_TOPIC, 0, 7, 0, 0, b"101", value, None, 3, len(value), headers)


def handler(analyzer: Any = None, **kwargs: Any) -> CongestionRequestHandler:
    return CongestionRequestHandler(
        analyzer or FakeCongestionAnalyzer(),
        now=lambda: WINDOW_END.astimezone(UTC) + timedelta(seconds=10),
        **kwargs,
    )


def body(out: Any) -> dict[str, Any]:
    return json.loads(out.value)


@pytest.mark.asyncio
async def test_success_result_is_wrapped_in_envelope_with_spot_key() -> None:
    outputs = await handler()(record(headers=(("trace_id", b"trace-123"),)))

    assert len(outputs) == 1
    result = outputs[0]
    assert result.topic == RESULT_TOPIC
    assert result.key == b"101"
    assert dict(result.headers) == {"trace_id": b"trace-123"}
    envelope = body(result)
    assert envelope["event_type"] == ANALYSIS_COMPLETED
    assert envelope["schema_version"] == 1
    assert envelope["event_id"] != "e6fad25e-bd51-4f6c-812f-33e01e2a8e34"
    assert datetime.fromisoformat(envelope["occurred_at"]) == datetime.fromisoformat(envelope["data"]["analyzed_at"])
    data = envelope["data"]
    assert "event_id" not in data and "schema_version" not in data
    assert data["spot_id"] == 101
    assert data["window_end"] == "2026-10-13T18:00:00+09:00"
    assert data["analysis_status"] == "SUCCESS"
    assert data["result"]["congestion_level"] == "MEDIUM"
    assert data["result"]["causes"] == [{"cause_code": "EVENT", "report_count": 1}]


@pytest.mark.asyncio
async def test_analysis_failure_publishes_failed_result_and_keeps_original_in_dlt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    message = record()

    outputs = await handler(FailingAnalyzer())(message)

    result, dead = outputs
    assert body(result)["data"]["analysis_status"] == "FAILED"
    assert body(result)["data"]["result"] is None
    assert body(result)["data"]["window_end"] == "2026-10-13T18:00:00+09:00"
    assert dead.topic == REQUEST_TOPIC + "-dlt"
    assert dead.value == message.value
    assert dict(dead.headers)["error_reason"] == b"analysis_failed"
    failed = [r for r in caplog.records
              if r.getMessage().startswith("congestion analysis failed:")]
    assert len(failed) == 1
    assert failed[0].levelno == logging.ERROR


@pytest.mark.asyncio
async def test_analysis_over_time_limit_publishes_failed_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    outputs = await handler(SlowAnalyzer(), timeout=0.05)(
        record(headers=(("trace_id", b"timeout-trace"),))
    )

    result, dead = outputs
    assert body(result)["data"]["analysis_status"] == "FAILED"
    assert dict(dead.headers)["error_reason"] == b"analysis_timeout"
    failed = [r for r in caplog.records
              if r.getMessage().startswith("congestion analysis failed:")]
    assert len(failed) == 1
    assert failed[0].levelno == logging.ERROR
    assert re.search(r"code=ANALYSIS_FAILED reason=timeout ms=\d+", failed[0].getMessage())
    assert failed[0].getMessage().endswith(" request_id=timeout-trace")


@pytest.mark.asyncio
async def test_external_cancellation_is_logged_and_propagated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    analyzer = SlowAnalyzer()
    task = asyncio.create_task(handler(analyzer)(record(headers=(("trace_id", b"stop-trace"),))))
    await asyncio.wait_for(analyzer.started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    failed = [r for r in caplog.records
              if r.getMessage().startswith("congestion analysis failed:")]
    assert failed == []
    cancelled = [r for r in caplog.records
                 if r.getMessage().startswith("congestion analysis cancelled:")]
    assert len(cancelled) == 1
    assert cancelled[0].levelno == logging.WARNING
    assert re.search(r"spot_id=101 event_id=\S+ ms=\d+", cancelled[0].getMessage())
    assert "code=" not in cancelled[0].getMessage()
    assert "reason=" not in cancelled[0].getMessage()
    assert cancelled[0].getMessage().endswith(" request_id=stop-trace")


@pytest.mark.asyncio
async def test_stale_window_is_not_analyzed() -> None:
    analyzer = CountingAnalyzer()
    late = CongestionRequestHandler(
        analyzer,
        stale_after=timedelta(minutes=60),
        now=lambda: WINDOW_END.astimezone(UTC) + timedelta(minutes=61),
    )

    outputs = await late(record())

    assert analyzer.calls == 0
    assert len(outputs) == 1
    assert body(outputs[0])["data"]["analysis_status"] == "FAILED"


@pytest.mark.asyncio
async def test_other_event_type_is_skipped() -> None:
    assert await handler()(record(event_type="congestion.analysis.other")) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "reason"),
    [
        (record(value=b"not json"), "not_json"),
        (record(value=b'{"event_id":"x"}'), "missing_event_type"),
        (record(schema_version=2), "unsupported_schema_version"),
        (record(data={"spot_id": 101}), "request_invalid"),
    ],
)
async def test_unreadable_request_goes_to_dlt_without_result(message: ConsumerRecord, reason: str) -> None:
    with pytest.raises(InvalidMessage) as exc:
        await handler()(message)

    assert exc.value.reason == reason


@pytest.mark.asyncio
async def test_timeout_keeps_request_context_for_unsafe_event_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw = json.loads(record().value)
    raw["event_id"] = "bad\nevent"
    await handler(SlowAnalyzer(), timeout=0.01)(record(value=json.dumps(raw).encode()))
    logs = [r.getMessage() for r in caplog.records
            if r.getMessage().startswith(("congestion analysis started:",
                                          "congestion analysis failed:"))]
    assert len(logs) == 2
    ids = [re.search(r" request_id=([0-9a-f]{12})$", line) for line in logs]
    assert all(ids)
    assert ids[0].group(1) == ids[1].group(1)
    assert all("event_id=-" in line and "\n" not in line for line in logs)
