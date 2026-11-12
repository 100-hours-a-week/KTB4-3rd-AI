"""송금 분석 요청 data 를 분석해 결과 상태 · 오류 코드 · 송금액으로 바꾸는지 검증한다."""

from typing import Any

import pytest

from app.core.exceptions import (
    ImageFetchError,
    ImageSizeError,
    ImageTypeError,
    JudgeParseError,
    ModelNotReady,
    QueueTimeout,
    UpstreamError,
)
from app.features.receipt.kafka_handler import TRANSFER_COMPLETED, TransferAnalysisHandler
from app.kafka.envelope import InvalidMessage

IMAGE_URL = "https://moyeota.s3.ap-northeast-2.amazonaws.com/transfer/a.jpg?X-Amz-Expires=1800"
RECEIPT = {"amount_cost": 6000, "transfer_info": "이체 완료", "sender_name": "홍길동", "send_time": "11:40"}


def request_data(**overrides: Any) -> dict[str, Any]:
    return {"settlement_id": 77, "user_id": 43, "attempt": 1, "image_url": IMAGE_URL, **overrides}


def returning(result: Any) -> Any:
    async def analyze(url: str) -> Any:
        return result

    return analyze


def raising(exc: Exception) -> Any:
    async def analyze(url: str) -> dict[str, Any]:
        raise exc

    return analyze


def handler(analyze: Any = None) -> TransferAnalysisHandler:
    return TransferAnalysisHandler(analyze or returning(RECEIPT))


@pytest.mark.asyncio
async def test_read_result_keeps_settlement_user_and_attempt() -> None:
    seen: list[str] = []

    async def analyze(url: str) -> dict[str, Any]:
        seen.append(url)
        return RECEIPT

    event_type, data = await handler(analyze)(request_data(attempt=2))

    assert seen == [IMAGE_URL]
    assert event_type == TRANSFER_COMPLETED
    assert data == {
        "settlement_id": 77,
        "user_id": 43,
        "attempt": 2,
        "analysis_status": "READ",
        "error_code": None,
        "amount_cost": 6000,
    }
    assert list(data) == ["settlement_id", "user_id", "attempt", "analysis_status", "error_code", "amount_cost"]


@pytest.mark.asyncio
async def test_receipt_details_are_not_passed_on() -> None:
    _, data = await handler()(request_data())

    assert not {"sender_name", "send_time", "transfer_info"} & data.keys()


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [JudgeParseError(), ImageSizeError(), ImageTypeError()])
async def test_photo_problem_is_unreadable(exc: Exception) -> None:
    _, data = await handler(raising(exc))(request_data())

    assert (data["analysis_status"], data["error_code"], data["amount_cost"]) == ("UNREADABLE", exc.code, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [ImageFetchError(), ModelNotReady(), QueueTimeout(), UpstreamError()])
async def test_server_side_error_is_system_failure(exc: Exception) -> None:
    _, data = await handler(raising(exc))(request_data())

    assert (data["analysis_status"], data["error_code"], data["amount_cost"]) == ("SYSTEM_FAILURE", exc.code, None)


@pytest.mark.asyncio
async def test_unexpected_error_is_system_failure() -> None:
    _, data = await handler(raising(RuntimeError("boom")))(request_data())

    assert (data["analysis_status"], data["error_code"]) == ("SYSTEM_FAILURE", "INTERNAL")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [{}, {"amount_cost": None}, {"amount_cost": "6000"}, {"amount_cost": True}, None],
)
async def test_missing_amount_cost_is_system_failure(result: Any) -> None:
    _, data = await handler(returning(result))(request_data())

    assert (data["analysis_status"], data["error_code"], data["amount_cost"]) == ("SYSTEM_FAILURE", "INTERNAL", None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        request_data(settlement_id="77"),
        {"settlement_id": 77, "attempt": 1, "image_url": IMAGE_URL},
        {"settlement_id": 77, "user_id": 43, "image_url": IMAGE_URL},
        request_data(attempt=0),
        request_data(attempt="1"),
        request_data(image_url=""),
    ],
)
async def test_invalid_request_goes_to_dlt(data: dict[str, Any]) -> None:
    calls: list[str] = []

    async def analyze(url: str) -> dict[str, Any]:
        calls.append(url)
        return RECEIPT

    with pytest.raises(InvalidMessage):
        await handler(analyze)(data)
    assert calls == []
