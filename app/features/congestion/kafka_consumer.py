import asyncio
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import uuid4

from aiokafka import ConsumerRecord  # type: ignore[import-untyped]
from pydantic import ValidationError

from ...core.logging import SAFE_ID, get_logger, request_id
from ...kafka.config import KafkaSettings
from ...kafka.envelope import InvalidMessage, read_envelope, write_envelope
from ...kafka.worker import KafkaWorker, OutgoingRecord, dead_letter
from .models import (
    CongestionAnalysisFailure,
    CongestionAnalysisMessage,
    CongestionAnalysisSuccess,
)
from .service import CongestionAnalyzer, analyze_message, failure_result, get_analyzer

logger = get_logger(__name__)

REQUEST_TOPIC = "congestion.analysis.request"
RESULT_TOPIC = "congestion.analysis.result"
GROUP_ID = "congestion-analyzer"
ANALYSIS_REQUESTED = "congestion.analysis.requested"
ANALYSIS_COMPLETED = "congestion.analysis.completed"
SCHEMA_VERSION = 1
TRACE_HEADER = "trace_id"


class CongestionRequestHandler:
    def __init__(
        self,
        analyzer: CongestionAnalyzer,
        *,
        timeout: float = 240.0,
        stale_after: timedelta = timedelta(minutes=60),
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.analyzer = analyzer
        self.timeout = timeout
        self.stale_after = stale_after
        self.now = now

    async def __call__(self, record: ConsumerRecord) -> list[OutgoingRecord]:
        envelope = read_envelope(record.value)
        if envelope.event_type != ANALYSIS_REQUESTED:
            logger.info(
                "congestion request skipped: event_id=%s event_type=%s reason=other_event",
                _safe(envelope.event_id) or "-",
                _safe(envelope.event_type) or "-",
            )
            return []
        if envelope.schema_version != SCHEMA_VERSION:
            raise InvalidMessage("unsupported_schema_version")
        trace_id = _trace_id(record)
        token = request_id.set(trace_id or _safe(envelope.event_id) or uuid4().hex[:12])
        try:
            message = _message(envelope.data, envelope.event_id, envelope.schema_version)
            outputs: list[OutgoingRecord] = []
            if self.now() - message.window_end > self.stale_after:
                logger.warning(
                    "congestion request skipped: spot_id=%s window_end=%s reason=stale stale_after=%s",
                    message.spot_id,
                    message.window_end.isoformat(),
                    self.stale_after,
                )
                result: CongestionAnalysisSuccess | CongestionAnalysisFailure = failure_result(message)
            else:
                started = monotonic()
                try:
                    result = await asyncio.wait_for(analyze_message(message, self.analyzer), self.timeout)
                except asyncio.CancelledError:
                    logger.warning(
                        "congestion analysis cancelled: spot_id=%s event_id=%s ms=%s",
                        message.spot_id,
                        _safe(message.event_id) or "-",
                        int((monotonic() - started) * 1000),
                    )
                    raise
                except TimeoutError:
                    logger.error(
                        "congestion analysis failed: spot_id=%s event_id=%s "
                        "code=ANALYSIS_FAILED reason=timeout ms=%s",
                        message.spot_id,
                        _safe(message.event_id) or "-",
                        int((monotonic() - started) * 1000),
                    )
                    result = failure_result(message)
                    outputs.append(dead_letter(record, "analysis_timeout"))
                except Exception:  # noqa: BLE001 - 예상 밖 분석 실패도 결과와 DLT로 남긴다.
                    result = failure_result(message)
                    outputs.append(dead_letter(record, "analysis_failed"))
            return [_result_record(result, trace_id), *outputs]
        finally:
            request_id.reset(token)


def _message(data: Mapping[str, object], event_id: str, schema_version: int) -> CongestionAnalysisMessage:
    try:
        return CongestionAnalysisMessage.model_validate(
            {**data, "event_id": event_id, "schema_version": schema_version}
        )
    except ValidationError as exc:
        fields = ",".join(sorted({".".join(str(part) for part in error["loc"][:1]) for error in exc.errors()}))
        logger.warning("congestion request is invalid: fields=%s errors=%s", fields or "-", exc.error_count())
        raise InvalidMessage("request_invalid") from exc


def _result_record(
    result: CongestionAnalysisSuccess | CongestionAnalysisFailure, trace_id: str | None
) -> OutgoingRecord:
    data = result.model_dump(mode="json", exclude={"event_id", "schema_version"})
    value = write_envelope(ANALYSIS_COMPLETED, data, result.analyzed_at, event_id=result.event_id)
    headers = ((TRACE_HEADER, trace_id.encode()),) if trace_id else ()
    return OutgoingRecord(RESULT_TOPIC, value, str(result.spot_id).encode(), headers)


def _trace_id(record: ConsumerRecord) -> str | None:
    for key, value in record.headers or ():
        if key == TRACE_HEADER and value:
            try:
                return _safe(value.decode())
            except UnicodeDecodeError:
                return None
    return None


def _safe(value: str) -> str | None:
    return value if SAFE_ID.match(value) else None


def create_worker(settings: KafkaSettings, env: Mapping[str, str] = os.environ) -> KafkaWorker:
    timeout = float(env.get("CONGESTION_ANALYSIS_TIMEOUT_SECONDS", "240"))
    handler = CongestionRequestHandler(
        get_analyzer(),
        timeout=timeout,
        stale_after=timedelta(minutes=float(env.get("CONGESTION_STALE_AFTER_MINUTES", "60"))),
    )
    return KafkaWorker(
        settings,
        REQUEST_TOPIC,
        GROUP_ID,
        handler,
        max_in_flight=int(env.get("CONGESTION_MAX_IN_FLIGHT", "10")),
        max_poll_interval_ms=int((timeout + 60) * 1000),
    )
