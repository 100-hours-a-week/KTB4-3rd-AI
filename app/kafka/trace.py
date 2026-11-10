from aiokafka import ConsumerRecord  # type: ignore[import-untyped]

from ..core.logging import SAFE_ID

TRACE_HEADER = "trace_id"


def safe_id(value: str) -> str | None:
    return value if SAFE_ID.match(value) else None


def trace_id_of(record: ConsumerRecord) -> str | None:
    for key, value in record.headers or ():
        if key == TRACE_HEADER and value:
            try:
                return safe_id(value.decode())
            except UnicodeDecodeError:
                return None
    return None


def trace_headers(trace_id: str | None) -> tuple[tuple[str, bytes], ...]:
    return ((TRACE_HEADER, trace_id.encode()),) if trace_id else ()
