import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, ConsumerRecord
from aiokafka.errors import KafkaConnectionError
from aiokafka.helpers import create_ssl_context

from ..core.logging import get_logger
from .config import KafkaSettings
from .envelope import InvalidMessage

logger = get_logger(__name__)

DLT_SUFFIX = "-dlt"
Headers = Sequence[tuple[str, bytes]]


@dataclass(frozen=True)
class OutgoingRecord:
    topic: str
    value: bytes
    key: bytes | None = None
    headers: Headers = field(default_factory=tuple)


Handler = Callable[[ConsumerRecord], Awaitable[list[OutgoingRecord]]]


def dead_letter(record: ConsumerRecord, reason: str) -> OutgoingRecord:
    headers = [*(record.headers or ()), ("error_reason", reason.encode())]
    return OutgoingRecord(record.topic + DLT_SUFFIX, record.value, record.key, tuple(headers))


class KafkaWorker:
    def __init__(
        self,
        settings: KafkaSettings,
        topic: str,
        group_id: str,
        handler: Handler,
        *,
        max_in_flight: int = 10,
        max_poll_interval_ms: int = 300_000,
        retry_delay: float = 5.0,
        shutdown_timeout: float = 40.0,
    ) -> None:
        self.settings = settings
        self.topic = topic
        self.group_id = group_id
        self.handler = handler
        self.max_in_flight = max_in_flight
        self.max_poll_interval_ms = max_poll_interval_ms
        self.retry_delay = retry_delay
        self.shutdown_timeout = shutdown_timeout
        self._stopping = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._consumer: AIOKafkaConsumer | None = None
        self._producer: AIOKafkaProducer | None = None

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"kafka-worker-{self.topic}")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._task), self.shutdown_timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "kafka worker stop timed out: topic=%s timeout=%ss hint=커밋하지 않은 메시지는 다른 태스크가 다시 받는다",
                self.topic,
                self.shutdown_timeout,
            )
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self._open()
                logger.info("kafka worker started: topic=%s group=%s", self.topic, self.group_id)
                await self._consume()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "kafka worker failed: topic=%s reason=%s retry_in=%ss hint=커밋한 위치부터 다시 받는다",
                    self.topic,
                    type(exc).__name__,
                    self.retry_delay,
                    exc_info=not isinstance(exc, KafkaConnectionError),
                )
                await self._close()
                await self._wait_or_stop(self.retry_delay)
            finally:
                if self._stopping.is_set():
                    await self._close()
        logger.info("kafka worker stopped: topic=%s", self.topic)

    async def _open(self) -> None:
        common: dict[str, Any] = {
            "bootstrap_servers": self.settings.bootstrap_servers,
            "client_id": self.settings.client_id,
            "security_protocol": self.settings.security_protocol,
        }
        if self.settings.security_protocol == "SSL":
            common["ssl_context"] = create_ssl_context()
        self._producer = AIOKafkaProducer(**common, acks="all", enable_idempotence=True)
        self._consumer = AIOKafkaConsumer(
            self.topic,
            **common,
            group_id=self.group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            max_poll_records=self.max_in_flight,
            max_poll_interval_ms=self.max_poll_interval_ms,
        )
        await self._producer.start()
        await self._consumer.start()

    async def _close(self) -> None:
        consumer, producer = self._consumer, self._producer
        self._consumer = self._producer = None
        for client in (consumer, producer):
            if client is None:
                continue
            try:
                await client.stop()
            except Exception as exc:
                logger.warning("kafka client close failed: topic=%s reason=%s", self.topic, type(exc).__name__)

    async def _consume(self) -> None:
        assert self._consumer is not None and self._producer is not None
        while not self._stopping.is_set():
            batch = await self._consumer.getmany(timeout_ms=1000, max_records=self.max_in_flight)
            records = [record for records in batch.values() for record in records]
            if not records:
                continue
            outputs = await asyncio.gather(*(self._process(record) for record in records))
            sends = [
                await self._producer.send(out.topic, out.value, key=out.key, headers=list(out.headers))
                for outs in outputs
                for out in outs
            ]
            await asyncio.gather(*sends)
            await self._consumer.commit(
                {partition: records[-1].offset + 1 for partition, records in batch.items()}
            )

    async def _process(self, record: ConsumerRecord) -> list[OutgoingRecord]:
        try:
            return await self.handler(record)
        except InvalidMessage as exc:
            logger.warning(
                "kafka message sent to dlt: topic=%s partition=%s offset=%s reason=%s",
                record.topic,
                record.partition,
                record.offset,
                exc.reason,
            )
            return [dead_letter(record, exc.reason)]

    async def _wait_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stopping.wait(), seconds)
        except asyncio.TimeoutError:
            pass

