from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import BaseModel, Field, StrictInt, StringConstraints, ValidationError

from ...core.exceptions import AppError
from ...core.logging import get_logger
from ...kafka.envelope import InvalidMessage
from ..taxi_meter.kafka_handler import INTERNAL, READ, SYSTEM_FAILURE, UNREADABLE, UNREADABLE_CODES, Analyze

logger = get_logger(__name__)

TRANSFER_REQUESTED = "transfer.analysis.requested"
TRANSFER_COMPLETED = "transfer.analysis.completed"
TRANSFER_SCHEMA_VERSION = 1


class TransferAnalysisRequest(BaseModel):
    settlement_id: StrictInt
    user_id: StrictInt
    attempt: Annotated[StrictInt, Field(ge=1)]
    image_url: Annotated[str, StringConstraints(min_length=1)]


class TransferAnalysisHandler:
    schema_version = TRANSFER_SCHEMA_VERSION

    def __init__(self, analyze: Analyze) -> None:
        self.analyze = analyze

    async def __call__(self, data: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        request = _request(data)
        status, error_code, amount_cost = await self._analyze(request)
        logger.info(
            "transfer analysis finished: settlement_id=%s user_id=%s attempt=%s status=%s code=%s",
            request.settlement_id,
            request.user_id,
            request.attempt,
            status,
            error_code or "-",
        )
        return TRANSFER_COMPLETED, {
            "settlement_id": request.settlement_id,
            "user_id": request.user_id,
            "attempt": request.attempt,
            "analysis_status": status,
            "error_code": error_code,
            "amount_cost": amount_cost,
        }

    async def _analyze(self, request: TransferAnalysisRequest) -> tuple[str, str | None, int | None]:
        try:
            result = await self.analyze(request.image_url)
        except AppError as exc:
            return (UNREADABLE if exc.code in UNREADABLE_CODES else SYSTEM_FAILURE), exc.code, None
        except Exception:
            logger.exception(
                "transfer analysis failed: settlement_id=%s user_id=%s", request.settlement_id, request.user_id
            )
            return SYSTEM_FAILURE, INTERNAL, None
        amount_cost = result.get("amount_cost") if isinstance(result, Mapping) else None
        if type(amount_cost) is not int:
            logger.error(
                "transfer analysis returned no amount_cost: settlement_id=%s user_id=%s",
                request.settlement_id,
                request.user_id,
            )
            return SYSTEM_FAILURE, INTERNAL, None
        return READ, None, amount_cost


def _request(data: Mapping[str, Any]) -> TransferAnalysisRequest:
    try:
        return TransferAnalysisRequest.model_validate(data)
    except ValidationError as exc:
        fields = ",".join(sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]}))
        logger.warning("transfer request is invalid: fields=%s errors=%s", fields or "-", exc.error_count())
        raise InvalidMessage("request_invalid") from exc
