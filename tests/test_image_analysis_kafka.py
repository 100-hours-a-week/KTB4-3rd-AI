"""이미지 분석 요청 토픽에서 event_type 별 처리기를 골라 결과 envelope 를 만드는지 검증한다."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from aiokafka import ConsumerRecord

from app.features.image_analysis.kafka_consumer import REQUEST_TOPIC, RESULT_TOPIC, ImageAnalysisRequestHandler
from app.kafka.envelope import InvalidMessage

NOW = datetime(2026, 11, 9, 5, 30, 15, tzinfo=UTC)
DATA = {"settlement_id": 101}


def record(
    *,
    event_type: str = "meter.analysis.requested",
    schema_version: int = 1,
    headers: tuple[tuple[str, bytes], ...] = (),
) -> ConsumerRecord:
    value = json.dumps(
        {
            "event_id": "7d4f1f5e-3c8a-4d27-9b1e-2f0c6a8e5b11",
            "event_type": event_type,
            "schema_version": schema_version,
            "occurred_at": "2026-11-09T14:30:05+09:00",
            "data": DATA,
        }
    ).encode()
    return ConsumerRecord(REQUEST_TOPIC, 0, 7, 0, 0, b"101:9", value, None, 5, len(value), headers)


class Route:
    def __init__(self, schema_version: int = 1) -> None:
        self.schema_version = schema_version
        self.received: list[Any] = []

    async def __call__(self, data: Any) -> tuple[str, dict[str, Any]]:
        self.received.append(data)
        return "meter.analysis.completed", {"settlement_id": data["settlement_id"], "analysis_status": "READ"}


def handler(route: Route, receipt: Route | None = None) -> ImageAnalysisRequestHandler:
    routes = {"meter.analysis.requested": route}
    if receipt is not None:
        routes["receipt.analysis.requested"] = receipt
    return ImageAnalysisRequestHandler(routes, now=lambda: NOW)


@pytest.mark.asyncio
async def test_result_is_wrapped_in_envelope_with_request_key_and_trace() -> None:
    route = Route()

    outputs = await handler(route)(record(headers=(("trace_id", b"trace-123"),)))

    assert route.received == [DATA]
    assert len(outputs) == 1
    result = outputs[0]
    assert (result.topic, result.key) == (RESULT_TOPIC, b"101:9")
    assert dict(result.headers) == {"trace_id": b"trace-123"}
    body = json.loads(result.value)
    assert body["event_type"] == "meter.analysis.completed"
    assert body["schema_version"] == 1
    assert body["occurred_at"] == NOW.isoformat()
    assert body["data"] == {"settlement_id": 101, "analysis_status": "READ"}


@pytest.mark.asyncio
async def test_unsafe_trace_header_is_not_passed_on() -> None:
    outputs = await handler(Route())(record(headers=(("trace_id", b"bad trace!"),)))

    assert dict(outputs[0].headers) == {}


@pytest.mark.asyncio
async def test_unknown_schema_version_goes_to_dlt() -> None:
    route = Route()

    with pytest.raises(InvalidMessage):
        await handler(route)(record(schema_version=2))
    assert route.received == []


@pytest.mark.asyncio
async def test_schema_version_is_checked_and_written_per_event_type() -> None:
    meter, receipt = Route(schema_version=1), Route(schema_version=2)
    routes = handler(meter, receipt)

    outputs = await routes(record(event_type="receipt.analysis.requested", schema_version=2))
    with pytest.raises(InvalidMessage):
        await routes(record(event_type="receipt.analysis.requested", schema_version=1))
    await routes(record(schema_version=1))

    assert json.loads(outputs[0].value)["schema_version"] == 2
    assert len(receipt.received) == 1
    assert len(meter.received) == 1


@pytest.mark.asyncio
async def test_other_event_is_skipped() -> None:
    route = Route()

    assert await handler(route)(record(event_type="receipt.analysis.requested")) == []
    assert route.received == []
