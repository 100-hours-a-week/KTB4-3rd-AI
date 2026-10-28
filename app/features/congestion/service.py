"""스팟별 분석기 경계와 요청 결과 조립."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from ...core.exceptions import ModelNotReady, UpstreamError
from ...core.logging import get_logger
from .aggregator import aggregate_signals
from .config import RELEVANCE_POLICY
from .decisions import DecisionsRelevanceScorer
from .extractor import ModelOutputInvalid, call_api_model
from .models import (
    CongestionAnalysisFailure,
    CongestionAnalysisMessage,
    CongestionAnalysisResult,
    CongestionAnalysisSuccess,
    CongestionCause,
    CongestionResult,
    CongestionResultWithSummary,
)
from .preprocessor import prepare_spot
from .relevance import (
    RelevanceOutputInvalid,
    RelevancePolicy,
    RelevanceScorer,
    classify_spot,
)
from .spot_extractor import assess_spot
from .spot_input import build_spot_input
from .summarizer import summarize_result

logger = get_logger(__name__)


class AnalysisError(Exception):
    """분석기가 보고하는 스팟 단위 기술적 실패. 내부 메시지는 응답하지 않는다."""


class CongestionAnalyzer(Protocol):
    def analyze(self, message: CongestionAnalysisMessage) -> CongestionResult: ...


class FakeCongestionAnalyzer:
    """테스트에서만 사용하는 고정 예시 분석기."""

    def analyze(self, message: CongestionAnalysisMessage) -> CongestionResult:
        return CongestionResult(
            congestion_level="MEDIUM",
            report_count=1,
            causes=[CongestionCause(cause_code="EVENT", report_count=1)],
        )


class LLMCongestionAnalyzer:
    """후보를 선별·분류한 스팟을 한 번 추출하고 코드로 집계한다."""

    def __init__(
        self,
        *,
        score: RelevanceScorer | None = None,
        policy: RelevancePolicy | None = None,
        complete: Callable[[str], str] = call_api_model,
    ) -> None:
        if (score is None) != (policy is None):
            raise ValueError("score and policy must be provided together")
        self._score = score
        self._policy = policy
        self._complete = complete

    def analyze(self, message: CongestionAnalysisMessage) -> CongestionResult:
        prepared = prepare_spot(message, message.window_end)
        if not prepared.candidates:
            return aggregate_signals([])
        try:
            score, policy = self._configured_scorer()
            classified = classify_spot(prepared, score, policy)
            assessed = assess_spot(build_spot_input(classified), self._complete)
        except (
            ModelNotReady,
            UpstreamError,
            ModelOutputInvalid,
            RelevanceOutputInvalid,
        ) as exc:
            raise AnalysisError("congestion analysis failed") from exc
        return aggregate_signals(assessed.signals)

    def _configured_scorer(self) -> tuple[RelevanceScorer, RelevancePolicy]:
        if self._score is not None and self._policy is not None:
            return self._score, self._policy
        return DecisionsRelevanceScorer(), RELEVANCE_POLICY


def get_analyzer() -> CongestionAnalyzer:
    return LLMCongestionAnalyzer()


def analyze_message(
    message: CongestionAnalysisMessage, analyzer: CongestionAnalyzer
) -> CongestionAnalysisResult:
    try:
        result = analyzer.analyze(message)
    except AnalysisError as exc:
        logger.warning(
            "congestion analysis failed: spot_id=%s event_id=%s error=%s",
            message.spot_id,
            message.event_id,
            type(exc.__cause__ if exc.__cause__ is not None else exc).__name__,
        )
        return CongestionAnalysisFailure(
            event_id=str(uuid4()),
            schema_version=1,
            window_end=message.window_end,
            spot_id=message.spot_id,
            analyzed_at=datetime.now(UTC),
        )
    return CongestionAnalysisSuccess(
        event_id=str(uuid4()),
        schema_version=1,
        window_end=message.window_end,
        spot_id=message.spot_id,
        result=CongestionResultWithSummary(
            congestion_level=result.congestion_level,
            report_count=result.report_count,
            causes=result.causes,
            summary=summarize_result(result),
        ),
        analyzed_at=datetime.now(UTC),
    )
