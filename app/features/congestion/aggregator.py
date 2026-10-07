"""판정이 끝난 제보를 작성자별 최신값으로 모아 스팟 결과를 계산한다."""

from collections import Counter
from collections.abc import Callable, Hashable, Iterable
from datetime import datetime
from typing import Literal, Protocol, TypeVar

from .models import CauseCode, CongestionCause, CongestionLevel, CongestionResult
from .preprocessor import is_in_window

Stage = Literal["LOW", "MEDIUM", "HIGH"]


class JudgedSignal(Protocol):
    """LLM 출력 형식과 독립적인 집계 입력 경계."""

    @property
    def author_key(self) -> str: ...

    @property
    def created_at(self) -> datetime: ...

    @property
    def congestion_level(self) -> Stage | None: ...

    @property
    def causes(self) -> frozenset[CauseCode]: ...


Vote = TypeVar("Vote", bound=Hashable)


def _latest_votes(
    signals: Iterable[JudgedSignal], value_of: Callable[[JudgedSignal], Vote | None]
) -> list[Vote]:
    latest: dict[str, tuple[datetime, set[Vote]]] = {}
    for signal in signals:
        value = value_of(signal)
        if value is None:
            continue
        current = latest.get(signal.author_key)
        if current is None or signal.created_at > current[0]:
            latest[signal.author_key] = (signal.created_at, {value})
        elif signal.created_at == current[0]:
            current[1].add(value)
    return [next(iter(values)) for _, values in latest.values() if len(values) == 1]


def _stage_of(signal: JudgedSignal) -> Stage | None:
    return signal.congestion_level


def _causes_of(signal: JudgedSignal) -> frozenset[CauseCode] | None:
    return signal.causes or None


def aggregate_signals(signals: Iterable[JudgedSignal], as_of: datetime) -> CongestionResult:
    current = [signal for signal in signals if is_in_window(signal.created_at, as_of)]
    for signal in current:
        level_value: object = signal.congestion_level
        if level_value == "UNKNOWN" or "UNKNOWN" in signal.causes:
            raise ValueError("UNKNOWN is an aggregate fallback, not an individual vote")
    stages: list[Stage] = _latest_votes(current, _stage_of)
    stage_counts = Counter(stages)
    if stage_counts:
        most_votes = max(stage_counts.values())
        winners = [stage for stage, count in stage_counts.items() if count == most_votes]
        level: CongestionLevel = winners[0] if len(winners) == 1 else "UNKNOWN"
    else:
        level = "UNKNOWN"

    cause_votes = _latest_votes(current, _causes_of)
    cause_counts: Counter[CauseCode] = Counter(
        cause for causes in cause_votes for cause in causes
    )
    if cause_counts:
        causes = [
            CongestionCause(cause_code=code, report_count=count)
            for code, count in sorted(cause_counts.items())
        ]
    elif stages:
        causes = [CongestionCause(cause_code="UNKNOWN", report_count=0)]
    else:
        causes = []
    return CongestionResult(
        congestion_level=level,
        report_count=len(stages),
        causes=causes,
    )
