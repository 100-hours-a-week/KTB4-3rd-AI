"""미터기 분석 요청 data 를 분석해 결과 상태 · 오류 코드 · 금액으로 바꾸는지 검증한다."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.exceptions import ImageFetchError, JudgeParseError, ModelNotReady, QueueTimeout
from app.features.taxi_meter.kafka_handler import METER_COMPLETED, MeterAnalysisHandler
from app.kafka.envelope import InvalidMessage

STARTED_AT = "2026-11-09T14:30:05+09:00"
NOW = datetime(2026, 11, 9, 5, 30, 15, tzinfo=UTC)
IMAGE_URL = "https://moyeota.s3.ap-northeast-2.amazonaws.com/meter/a.jpg?X-Amz-Expires=1800"


def request_data(**overrides: Any) -> dict[str, Any]:
    return {"settlement_id": 101, "analysis_started_at": STARTED_AT, "image_url": IMAGE_URL, **overrides}


def returning(result: dict[str, Any]) -> Any:
    async def analyze(url: str) -> dict[str, Any]:
        return result

    return analyze


def handler(analyze: Any = None) -> MeterAnalysisHandler:
    return MeterAnalysisHandler(analyze or returning({"cost": 18000}), now=lambda: NOW)


def raising(exc: Exception) -> Any:
    async def analyze(url: str) -> dict[str, int]:
        raise exc

    return analyze


@pytest.mark.asyncio
async def test_read_result_keeps_settlement_and_started_at() -> None:
    seen: list[str] = []

    async def analyze(url: str) -> dict[str, int]:
        seen.append(url)
        return {"cost": 18000}

    event_type, data = await handler(analyze)(request_data())

    assert seen == [IMAGE_URL]
    assert event_type == METER_COMPLETED
    assert data == {
        "settlement_id": 101,
        "analysis_started_at": STARTED_AT,
        "analysis_status": "READ",
        "error_code": None,
        "cost": 18000,
    }


@pytest.mark.asyncio
async def test_photo_problem_is_unreadable() -> None:
    _, data = await handler(raising(JudgeParseError()))(request_data())

    assert (data["analysis_status"], data["error_code"]) == ("UNREADABLE", "JUDGE_PARSE_FAILED")


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [ImageFetchError(), ModelNotReady(), QueueTimeout()])
async def test_server_side_error_is_system_failure(exc: Exception) -> None:
    _, data = await handler(raising(exc))(request_data())

    assert (data["analysis_status"], data["error_code"]) == ("SYSTEM_FAILURE", exc.code)


@pytest.mark.asyncio
async def test_unexpected_error_is_system_failure() -> None:
    _, data = await handler(raising(RuntimeError("boom")))(request_data())

    assert (data["analysis_status"], data["error_code"]) == ("SYSTEM_FAILURE", "INTERNAL")


@pytest.mark.asyncio
async def test_missing_cost_is_system_failure() -> None:
    _, data = await handler(returning({"cost": None}))(request_data())

    assert (data["analysis_status"], data["error_code"], data["cost"]) == ("SYSTEM_FAILURE", "INTERNAL", None)


@pytest.mark.asyncio
async def test_request_older_than_wait_limit_is_not_analyzed() -> None:
    calls: list[str] = []
    started = (NOW - timedelta(seconds=91)).isoformat()

    async def analyze(url: str) -> dict[str, int]:
        calls.append(url)
        return {"cost": 1}

    _, data = await handler(analyze)(
        request_data(analysis_started_at=started)
    )

    assert calls == []
    assert (data["analysis_status"], data["error_code"]) == ("SYSTEM_FAILURE", "QUEUE_WAIT_EXCEEDED")
    assert data["analysis_started_at"] == started


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        request_data(settlement_id="101"),
        request_data(analysis_started_at="2026-11-09T14:30:05"),
        request_data(image_url=""),
        {"settlement_id": 101},
    ],
)
async def test_invalid_request_goes_to_dlt(data: dict[str, Any]) -> None:
    with pytest.raises(InvalidMessage):
        await handler()(data)
