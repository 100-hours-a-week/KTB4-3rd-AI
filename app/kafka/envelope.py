import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4


class InvalidMessage(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Envelope:
    event_id: str
    event_type: str
    schema_version: int
    occurred_at: str
    data: dict[str, Any]


def read_envelope(value: bytes | None) -> Envelope:
    if not value:
        raise InvalidMessage("empty")
    try:
        body = json.loads(value)
    except (UnicodeDecodeError, ValueError) as exc:
        raise InvalidMessage("not_json") from exc
    if not isinstance(body, dict):
        raise InvalidMessage("not_object")
    event_id = body.get("event_id")
    event_type = body.get("event_type")
    schema_version = body.get("schema_version")
    occurred_at = body.get("occurred_at")
    data = body.get("data")
    if not isinstance(event_id, str) or not event_id.strip():
        raise InvalidMessage("missing_event_id")
    if not isinstance(event_type, str) or not event_type.strip():
        raise InvalidMessage("missing_event_type")
    if type(schema_version) is not int or schema_version < 1:
        raise InvalidMessage("missing_schema_version")
    if not isinstance(occurred_at, str) or not occurred_at.strip():
        raise InvalidMessage("missing_occurred_at")
    if not isinstance(data, dict):
        raise InvalidMessage("missing_data")
    return Envelope(event_id, event_type, schema_version, occurred_at, data)


def write_envelope(
    event_type: str,
    data: dict[str, Any],
    occurred_at: datetime,
    *,
    event_id: str | None = None,
    schema_version: int = 1,
) -> bytes:
    body = {
        "event_id": event_id or str(uuid4()),
        "event_type": event_type,
        "schema_version": schema_version,
        "occurred_at": occurred_at.isoformat(),
        "data": data,
    }
    return json.dumps(body, ensure_ascii=False).encode()
