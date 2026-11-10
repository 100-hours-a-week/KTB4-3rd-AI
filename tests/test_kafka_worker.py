"""실제 브로커로 Kafka 워커의 발행·DLT·커밋·재시도를 검증한다.

KAFKA_TEST_BOOTSTRAP_SERVERS(예: localhost:9092)가 있을 때만 실행한다.
"""

import asyncio
import os
from uuid import uuid4

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, ConsumerRecord, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient, NewTopic

from app.kafka.config import KafkaSettings
from app.kafka.envelope import InvalidMessage
from app.kafka.worker import KafkaWorker, OutgoingRecord

SERVERS = os.environ.get("KAFKA_TEST_BOOTSTRAP_SERVERS", "")

pytestmark = pytest.mark.skipif(not SERVERS, reason="KAFKA_TEST_BOOTSTRAP_SERVERS가 없다")


async def create_topics(*names: str) -> None:
    admin = AIOKafkaAdminClient(bootstrap_servers=SERVERS)
    await admin.start()
    try:
        await admin.create_topics([NewTopic(name, 1, 1) for name in names])
    finally:
        await admin.close()


async def produce(topic: str, *values: bytes) -> None:
    producer = AIOKafkaProducer(bootstrap_servers=SERVERS)
    await producer.start()
    try:
        for value in values:
            await producer.send_and_wait(topic, value, key=b"101")
    finally:
        await producer.stop()


async def read(topic: str, count: int) -> list[ConsumerRecord]:
    consumer = AIOKafkaConsumer(topic, bootstrap_servers=SERVERS, auto_offset_reset="earliest", group_id=None)
    await consumer.start()
    records: list[ConsumerRecord] = []
    try:
        async with asyncio.timeout(20):
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
async def test_worker_publishes_results_sends_unreadable_to_dlt_and_commits() -> None:
    topic = f"test.worker.{uuid4().hex[:8]}"
    group_id = f"{topic}.group"
    await create_topics(topic, f"{topic}.out", f"{topic}-dlt")
    await produce(topic, b"one", b"bad", b"two")

    async def handle(record: ConsumerRecord) -> list[OutgoingRecord]:
        if record.value == b"bad":
            raise InvalidMessage("not_json")
        return [OutgoingRecord(f"{topic}.out", record.value.upper(), record.key)]

    worker = KafkaWorker(KafkaSettings(SERVERS), topic, group_id, handle)
    worker.start()
    try:
        outputs = await read(f"{topic}.out", 2)
        dead = await read(f"{topic}-dlt", 1)
        await wait_committed(topic, group_id, 3)
    finally:
        await worker.stop()

    assert sorted(record.value for record in outputs) == [b"ONE", b"TWO"]
    assert dead[0].value == b"bad"
    assert dict(dead[0].headers)["error_reason"] == b"not_json"
    assert not worker.alive


@pytest.mark.asyncio
async def test_worker_retries_from_last_commit_after_unexpected_error() -> None:
    topic = f"test.worker.{uuid4().hex[:8]}"
    group_id = f"{topic}.group"
    await create_topics(topic, f"{topic}.out")
    await produce(topic, b"one")
    attempts = 0

    async def handle(record: ConsumerRecord) -> list[OutgoingRecord]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("broker hiccup")
        return [OutgoingRecord(f"{topic}.out", record.value, record.key)]

    worker = KafkaWorker(KafkaSettings(SERVERS), topic, group_id, handle, retry_delay=0.2)
    worker.start()
    try:
        outputs = await read(f"{topic}.out", 1)
        await wait_committed(topic, group_id, 1)
        assert worker.alive
    finally:
        await worker.stop()

    assert attempts == 2
    assert outputs[0].value == b"one"
