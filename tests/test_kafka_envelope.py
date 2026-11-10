"""Kafka 이벤트 envelope 읽기·쓰기와 연결 설정을 검증한다."""

import json
from datetime import datetime

import pytest

from app.kafka.config import KafkaSettings, kafka_settings
from app.kafka.envelope import InvalidMessage, read_envelope, write_envelope


def test_write_then_read_envelope() -> None:
    value = write_envelope(
        "congestion.analysis.completed",
        {"spot_id": 101, "summary": "출퇴근하는 사람이 많아요."},
        datetime.fromisoformat("2026-11-04T23:00:05+09:00"),
        event_id="abc",
    )

    envelope = read_envelope(value)

    assert envelope.event_id == "abc"
    assert envelope.event_type == "congestion.analysis.completed"
    assert envelope.schema_version == 1
    assert envelope.occurred_at == "2026-11-04T23:00:05+09:00"
    assert envelope.data == {"spot_id": 101, "summary": "출퇴근하는 사람이 많아요."}
    assert "출퇴근" in value.decode()


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({"event_type": "x", "schema_version": 1, "occurred_at": "t", "data": {}}, "missing_event_id"),
        ({"event_id": "a", "event_type": "x", "schema_version": "1", "occurred_at": "t", "data": {}}, "missing_schema_version"),
        ({"event_id": "a", "event_type": "x", "schema_version": 1, "data": {}}, "missing_occurred_at"),
        ({"event_id": "a", "event_type": "x", "schema_version": 1, "occurred_at": "t", "data": []}, "missing_data"),
    ],
)
def test_missing_envelope_field_is_invalid(body: dict, reason: str) -> None:
    with pytest.raises(InvalidMessage) as exc:
        read_envelope(json.dumps(body).encode())

    assert exc.value.reason == reason


def test_kafka_is_off_without_bootstrap_servers() -> None:
    assert kafka_settings({}) is None
    assert kafka_settings({"KAFKA_BOOTSTRAP_SERVERS": "kafka:9092"}) == KafkaSettings("kafka:9092")


def test_unknown_security_protocol_is_rejected() -> None:
    with pytest.raises(ValueError):
        kafka_settings({"KAFKA_BOOTSTRAP_SERVERS": "kafka:9092", "KAFKA_SECURITY_PROTOCOL": "SASL_SSL"})
