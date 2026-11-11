"""실제 브로커로 송금 분석 요청 → 결과 발행 · DLT · 커밋을 검증한다.

KAFKA_TEST_BOOTSTRAP_SERVERS(예: localhost:9092)가 있을 때만 실행한다.
"""

import asyncio
import json
import os
from typing import Any
from uuid import uuid4

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, ConsumerRecord, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient, NewTopic

from app.features.image_analysis import kafka_consumer
from app.features.image_analysis.kafka_consumer import ImageAnalysisRequestHandler
from app.features.receipt.kafka_handler import TRANSFER_REQUESTED, TransferAnalysisHandler
from app.kafka.config import KafkaSettings
from app.kafka.worker import KafkaWorker

SERVERS = os.environ.get("KAFKA_TEST_BOOTSTRAP_SERVERS", "")

pytestmark = pytest.mark.skipif(not SERVERS, reason="KAFKA_TEST_BOOTSTRAP_SERVERS가 없다")


def envelope(data: dict[str, Any]) -> bytes:
    return json.dumps(
        {
            "event_id": str(uuid4()),
            "event_type": TRANSFER_REQUESTED,
            "schema_version": 1,
            "occurred_at": "2026-11-11T11:40:00.456+09:00",
            "data": data,
        }
    ).encode()


async def create_topics(*names: str) -> None:
    admin = AIOKafkaAdminClient(bootstrap_servers=SERVERS)
    await admin.start()
    try:
        await admin.create_topics([NewTopic(name, 1, 1) for name in names])
    finally:
        await admin.close()


async def produce(topic: str, *records: tuple[bytes, bytes, list[tuple[str, bytes]]]) -> None:
    producer = AIOKafkaProducer(bootstrap_servers=SERVERS)
    await producer.start()
    try:
        for key, value, headers in records:
            await producer.send_and_wait(topic, value, key=key, headers=headers)
    finally:
        await producer.stop()


async def read(topic: str, count: int, seconds: float = 20) -> list[ConsumerRecord]:
    consumer = AIOKafkaConsumer(topic, bootstrap_servers=SERVERS, auto_offset_reset="earliest", group_id=None)
    await consumer.start()
    records: list[ConsumerRecord] = []
    try:
        async with asyncio.timeout(seconds):
            while len(records) < count:
                batch = await consumer.getmany(timeout_ms=500)
                records.extend(record for records in batch.values() for record in records)
    finally:
        await consumer.stop()
    return records


async def committed(topic: str, group_id: str) -> int | None:
    consumer = AIOKafkaConsumer(bootstrap_servers=SERVERS, group_id=group_id, enable_auto_commit=False)
    await consumer.start()
    try:
        return await consumer.committed(TopicPartition(topic, 0))
    finally:
        await consumer.stop()


async def wait_committed(topic: str, group_id: str, offset: int) -> None:
    async with asyncio.timeout(20):
        while await committed(topic, group_id) != offset:
            await asyncio.sleep(0.3)


@pytest.mark.asyncio
async def test_transfer_request_publishes_result_sends_invalid_to_dlt_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    topic = f"test.transfer.{uuid4().hex[:8]}"
    result_topic = f"{topic}.result"
    group_id = f"{topic}.group"
    await create_topics(topic, result_topic, f"{topic}-dlt")
    monkeypatch.setattr(kafka_consumer, "RESULT_TOPIC", result_topic)
    analyzed: list[str] = []

    async def analyze(image_url: str) -> dict[str, Any]:
        analyzed.append(image_url)
        return {"amount_cost": 6000, "sender_name": "홍길동", "send_time": "11:40", "transfer_info": "이체 완료"}

    good = {"settlement_id": 77, "user_id": 43, "attempt": 1, "image_url": "https://example.com/t.jpg"}
    bad = {"settlement_id": 77, "attempt": 1, "image_url": "https://example.com/t.jpg"}
    await produce(
        topic,
        (b"77:43", envelope(good), [("trace_id", b"trace-77")]),
        (b"77:44", envelope(bad), []),
    )

    def worker() -> KafkaWorker:
        handler = ImageAnalysisRequestHandler({TRANSFER_REQUESTED: TransferAnalysisHandler(analyze)})
        return KafkaWorker(KafkaSettings(SERVERS), topic, group_id, handler)

    first = worker()
    first.start()
    try:
        results = await read(result_topic, 1)
        dead = await read(f"{topic}-dlt", 1)
        await wait_committed(topic, group_id, 2)
    finally:
        await first.stop()

    assert results[0].key == b"77:43"
    assert dict(results[0].headers) == {"trace_id": b"trace-77"}
    body = json.loads(results[0].value)
    assert (body["event_type"], body["schema_version"]) == ("transfer.analysis.completed", 1)
    assert body["data"] == {
        "settlement_id": 77,
        "user_id": 43,
        "attempt": 1,
        "analysis_status": "READ",
        "error_code": None,
        "amount_cost": 6000,
    }
    assert dead[0].key == b"77:44"
    assert json.loads(dead[0].value)["data"] == bad
    assert dict(dead[0].headers)["error_reason"] == b"request_invalid"

    after_restart = {**good, "user_id": 45, "image_url": "https://example.com/t2.jpg"}
    await produce(topic, (b"77:45", envelope(after_restart), []))
    second = worker()
    second.start()
    try:
        results = await read(result_topic, 2)
        await wait_committed(topic, group_id, 3)
    finally:
        await second.stop()

    assert analyzed == ["https://example.com/t.jpg", "https://example.com/t2.jpg"]
    assert [record.key for record in results] == [b"77:43", b"77:45"]
