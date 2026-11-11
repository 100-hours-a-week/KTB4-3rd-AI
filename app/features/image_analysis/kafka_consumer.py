import os
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from aiokafka import ConsumerRecord  # type: ignore[import-untyped]

from ...core.logging import get_logger, request_id
from ...inner_queue.scheduler import KAFKA_SLOTS, Scheduler
from ...kafka.config import KafkaSettings
from ...kafka.envelope import InvalidMessage, read_envelope, write_envelope
from ...kafka.trace import safe_id, trace_headers, trace_id_of
from ...kafka.worker import KafkaWorker, OutgoingRecord
from ..receipt.kafka_handler import TRANSFER_REQUESTED, TransferAnalysisHandler
from ..receipt.main import KIND as RECEIPT
from ..taxi_meter.kafka_handler import METER_REQUESTED, MeterAnalysisHandler
from ..taxi_meter.main import KIND as TAXI_METER

logger = get_logger(__name__)

REQUEST_TOPIC = "settlement.image-analysis.request"
RESULT_TOPIC = "settlement.image-analysis.result"
GROUP_ID = "image-analyzer"


class Route(Protocol):
    schema_version: int

    def __call__(self, data: Mapping[str, Any]) -> Awaitable[tuple[str, dict[str, Any]]]: ...


class ImageAnalysisRequestHandler:
    def __init__(
        self,
        routes: Mapping[str, Route],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.routes = routes
        self.now = now

    async def __call__(self, record: ConsumerRecord) -> list[OutgoingRecord]:
        envelope = read_envelope(record.value)
        route = self.routes.get(envelope.event_type)
        if route is None:
            logger.info(
                "image analysis request skipped: event_id=%s event_type=%s reason=other_event",
                safe_id(envelope.event_id) or "-",
                safe_id(envelope.event_type) or "-",
            )
            return []
        if envelope.schema_version != route.schema_version:
            raise InvalidMessage("unsupported_schema_version")
        trace_id = trace_id_of(record)
        token = request_id.set(trace_id or safe_id(envelope.event_id) or uuid4().hex[:12])
        try:
            event_type, data = await route(envelope.data)
        finally:
            request_id.reset(token)
        value = write_envelope(event_type, data, self.now(), schema_version=route.schema_version)
        return [OutgoingRecord(RESULT_TOPIC, value, record.key, trace_headers(trace_id))]


def create_worker(
    settings: KafkaSettings, scheduler: Scheduler, env: Mapping[str, str] = os.environ
) -> KafkaWorker:
    meter = MeterAnalysisHandler.from_env(lambda image_url: scheduler.submit(TAXI_METER, image_url), env)
    transfer = TransferAnalysisHandler(lambda image_url: scheduler.submit(RECEIPT, image_url))
    handler = ImageAnalysisRequestHandler({METER_REQUESTED: meter, TRANSFER_REQUESTED: transfer})
    return KafkaWorker(
        settings,
        REQUEST_TOPIC,
        GROUP_ID,
        handler,
        max_in_flight=int(env.get("IMAGE_ANALYSIS_MAX_IN_FLIGHT", KAFKA_SLOTS)),
        max_poll_interval_ms=int((scheduler.deadline + 60) * 1000),
        shutdown_timeout=10,
    )
