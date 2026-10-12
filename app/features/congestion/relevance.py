"""혼잡 관련성 확률을 검증하고 무관한 후보만 제외한다."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Literal

from .preprocessor import CandidateSignal, PreparedSpot

SignalKey = tuple[Literal["post", "comment"], int]
RelevanceStatus = Literal["RELATED", "UNRELATED", "UNCERTAIN"]
RelevanceScorer = Callable[[PreparedSpot], Mapping[SignalKey, float]]


class RelevanceOutputInvalid(ValueError):
    """관련성 응답의 대상 ID나 확률이 올바르지 않다."""


def _valid_probability(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= 1
        and isfinite(value)
    )


@dataclass(frozen=True)
class RelevancePolicy:
    """평가로 정할 임계값을 명시적으로 받으며 기본값을 두지 않는다."""

    exclude_below: float
    related_at_or_above: float

    def __post_init__(self) -> None:
        if (
            not _valid_probability(self.exclude_below)
            or not _valid_probability(self.related_at_or_above)
            or self.exclude_below >= self.related_at_or_above
        ):
            raise ValueError("relevance thresholds must satisfy 0 <= lower < upper <= 1")


@dataclass(frozen=True)
class RelevanceDecision:
    candidate: CandidateSignal
    probability: float
    status: RelevanceStatus


@dataclass(frozen=True)
class ClassifiedSpot:
    prepared: PreparedSpot
    decisions: tuple[RelevanceDecision, ...]

    @property
    def selected_candidates(self) -> tuple[CandidateSignal, ...]:
        """관련성이 불확실한 후보도 후속 개별 판정을 받을 수 있도록 남긴다."""
        return tuple(item.candidate for item in self.decisions if item.status != "UNRELATED")


def classify_spot(
    prepared: PreparedSpot, score: RelevanceScorer, policy: RelevancePolicy
) -> ClassifiedSpot:
    """완전한 확률 응답을 확인한 뒤 요청 순서대로 후보를 선별한다."""
    if not prepared.candidates:
        return ClassifiedSpot(prepared=prepared, decisions=())

    scores = score(prepared)
    expected = {(item.signal_type, item.signal_id) for item in prepared.candidates}
    if set(scores) != expected:
        raise RelevanceOutputInvalid("relevance score IDs do not match candidates")

    decisions: list[RelevanceDecision] = []
    for candidate in prepared.candidates:
        probability = scores[(candidate.signal_type, candidate.signal_id)]
        if not _valid_probability(probability):
            raise RelevanceOutputInvalid("relevance probability is invalid")
        status: RelevanceStatus
        if probability < policy.exclude_below:
            status = "UNRELATED"
        elif probability >= policy.related_at_or_above:
            status = "RELATED"
        else:
            status = "UNCERTAIN"
        decisions.append(RelevanceDecision(candidate, probability, status))
    return ClassifiedSpot(prepared=prepared, decisions=tuple(decisions))
