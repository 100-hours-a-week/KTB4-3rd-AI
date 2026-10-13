"""판정이 끝난 제보를 작성자별 최신값으로 모아 스팟 결과를 계산한다."""

from collections import Counter
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Generic, Literal, Protocol, TypeVar

from .models import CauseCode, CongestionCause, CongestionLevel, CongestionResult
from .preprocessor import WINDOW, is_in_window

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


@dataclass(frozen=True)
class AuthorVote(Generic[Vote]):
    """작성자의 최신 유효 제보들과 동시각 충돌 여부를 보존한다."""

    author_key: str
    created_at: datetime
    signal_indices: tuple[int, ...]
    values: frozenset[Vote]

    @property
    def value(self) -> Vote | None:
        """동시각 값이 같을 때만 한 표로 인정한다."""
        return next(iter(self.values)) if len(self.values) == 1 else None


@dataclass(frozen=True)
class AggregationTrace:
    """실제 집계 결과와 그 계산에 사용한 작성자별 선택 기록."""

    result: CongestionResult
    stage_votes: tuple[AuthorVote[Stage], ...]
    cause_votes: tuple[AuthorVote[frozenset[CauseCode]], ...]
    stage_counts: dict[Stage, int]
    decision: Literal["UNIQUE_TOP", "TIE", "NO_VALID_STAGE"]


def _latest_votes(
    signals: Iterable[tuple[int, JudgedSignal]],
    value_of: Callable[[JudgedSignal], Vote | None],
) -> tuple[AuthorVote[Vote], ...]:
    latest: dict[str, tuple[datetime, set[Vote], list[int]]] = {}
    for index, signal in signals:
        value = value_of(signal)
        if value is None:
            continue
        current = latest.get(signal.author_key)
        if current is None or signal.created_at > current[0]:
            latest[signal.author_key] = (signal.created_at, {value}, [index])
        elif signal.created_at == current[0]:
            current[1].add(value)
            current[2].append(index)
    return tuple(
        AuthorVote(author, created_at, tuple(indices), frozenset(values))
        for author, (created_at, values, indices) in latest.items()
    )


def _stage_of(signal: JudgedSignal) -> Stage | None:
    return signal.congestion_level


def _causes_of(signal: JudgedSignal) -> frozenset[CauseCode] | None:
    return signal.causes or None


def aggregate_signals(
    signals: Iterable[JudgedSignal], as_of: datetime, *, window: timedelta = WINDOW
) -> CongestionResult:
    return aggregate_with_trace(signals, as_of, window=window).result


def aggregate_with_trace(
    signals: Iterable[JudgedSignal], as_of: datetime, *, window: timedelta = WINDOW
) -> AggregationTrace:
    """HTTP와 진단에 같은 선택·집계를 적용하고 입력의 원래 인덱스를 보존한다."""
    if window <= timedelta(0):
        raise ValueError("window must be positive")
    current = [
        (index, signal)
        for index, signal in enumerate(signals)
        if is_in_window(signal.created_at, as_of, window)
    ]
    for _, signal in current:
        level_value: object = signal.congestion_level
        if level_value == "UNKNOWN" or "UNKNOWN" in signal.causes:
            raise ValueError("UNKNOWN is an aggregate fallback, not an individual vote")
    stage_votes: tuple[AuthorVote[Stage], ...] = _latest_votes(current, _stage_of)
    stages = [value for vote in stage_votes if (value := vote.value) is not None]
    stage_counts = Counter(stages)
    decision: Literal["UNIQUE_TOP", "TIE", "NO_VALID_STAGE"]
    if stage_counts:
        most_votes = max(stage_counts.values())
        winners = [
            stage for stage, count in stage_counts.items() if count == most_votes
        ]
        level: CongestionLevel = winners[0] if len(winners) == 1 else "UNKNOWN"
        decision = "UNIQUE_TOP" if len(winners) == 1 else "TIE"
    else:
        level = "UNKNOWN"
        decision = "NO_VALID_STAGE"

    cause_votes = _latest_votes(current, _causes_of)
    cause_counts: Counter[CauseCode] = Counter(
        cause
        for vote in cause_votes
        if (values := vote.value) is not None
        for cause in values
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
    return AggregationTrace(
        result=CongestionResult(
            congestion_level=level,
            report_count=len(stages),
            causes=causes,
        ),
        stage_votes=stage_votes,
        cause_votes=cause_votes,
        stage_counts={
            "LOW": stage_counts["LOW"],
            "MEDIUM": stage_counts["MEDIUM"],
            "HIGH": stage_counts["HIGH"],
        },
        decision=decision,
    )
