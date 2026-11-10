from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, StrictInt, StringConstraints, ValidationError

from ...core.exceptions import AppError
from ...core.logging import get_logger
from ...kafka.envelope import InvalidMessage

logger = get_logger(__name__)

METER_REQUESTED = "meter.analysis.requested"
METER_COMPLETED = "meter.analysis.completed"
METER_SCHEMA_VERSION = 1

READ = "READ"
UNREADABLE = "UNREADABLE"
SYSTEM_FAILURE = "SYSTEM_FAILURE"

QUEUE_WAIT_EXCEEDED = "QUEUE_WAIT_EXCEEDED"
INTERNAL = "INTERNAL"
UNREADABLE_CODES = frozenset({"IMAGE_SIZE_INVALID", "IMAGE_TYPE_INVALID", "JUDGE_PARSE_FAILED"})

Analyze = Callable[[str], Awaitable[Mapping[str, Any]]]


class MeterAnalysisRequest(BaseModel):
    settlement_id: StrictInt
    analysis_started_at: AwareDatetime
    image_url: Annotated[str, StringConstraints(min_length=1)]


class MeterAnalysisHandler:
    schema_version = METER_SCHEMA_VERSION

    def __init__(
        self,
        analyze: Analyze,
        *,
        wait_limit: timedelta = timedelta(seconds=90),
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.analyze = analyze
        self.wait_limit = wait_limit
        self.now = now

    @classmethod
    def from_env(cls, analyze: Analyze, env: Mapping[str, str]) -> "MeterAnalysisHandler":
        return cls(analyze, wait_limit=timedelta(seconds=float(env.get("METER_QUEUE_WAIT_LIMIT_SECONDS", "90"))))

    async def __call__(self, data: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        request = _request(data)
        status, error_code, cost = await self._analyze(request)
        logger.info(
            "meter analysis finished: settlement_id=%s status=%s code=%s",
            request.settlement_id,
            status,
            error_code or "-",
        )
        return METER_COMPLETED, {
            "settlement_id": request.settlement_id,
            "analysis_started_at": data["analysis_started_at"],
            "analysis_status": status,
            "error_code": error_code,
            "cost": cost,
        }

    async def _analyze(self, request: MeterAnalysisRequest) -> tuple[str, str | None, int | None]:
        waited = self.now() - request.analysis_started_at
        if waited > self.wait_limit:
            logger.warning(
                "meter analysis skipped: settlement_id=%s waited=%ss reason=queue_wait_exceeded",
                request.settlement_id,
                int(waited.total_seconds()),
            )
            return SYSTEM_FAILURE, QUEUE_WAIT_EXCEEDED, None
        try:
            result = await self.analyze(request.image_url)
        except AppError as exc:
            return (UNREADABLE if exc.code in UNREADABLE_CODES else SYSTEM_FAILURE), exc.code, None
        except Exception:
            logger.exception("meter analysis failed: settlement_id=%s", request.settlement_id)
            return SYSTEM_FAILURE, INTERNAL, None
        cost = result.get("cost") if isinstance(result, Mapping) else None
        if type(cost) is not int:
            logger.error("meter analysis returned no cost: settlement_id=%s", request.settlement_id)
            return SYSTEM_FAILURE, INTERNAL, None
        return READ, None, cost


def _request(data: Mapping[str, Any]) -> MeterAnalysisRequest:
    try:
        return MeterAnalysisRequest.model_validate(data)
    except ValidationError as exc:
        fields = ",".join(sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]}))
        logger.warning("meter request is invalid: fields=%s errors=%s", fields or "-", exc.error_count())
        raise InvalidMessage("request_invalid") from exc
