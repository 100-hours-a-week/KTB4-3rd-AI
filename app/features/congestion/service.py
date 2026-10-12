"""스팟별 분석기 경계와 요청 결과 조립."""

import os
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from typing import Protocol

from ...core.exceptions import ModelNotReady, UpstreamError
from .aggregator import aggregate_signals
from .decisions import DecisionsRelevanceScorer
from .extractor import ModelOutputInvalid, call_api_model
from .models import (
    CongestionAnalysisRequest,
    CongestionAnalysisResponse,
    CongestionCause,
    CongestionResult,
    CongestionResultWithSummary,
    CongestionSpotFailure,
    CongestionSpotInput,
    CongestionSpotResult,
    CongestionSpotSuccess,
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

MAX_CONCURRENT_SPOTS = 3


class AnalysisError(Exception):
    """분석기가 보고하는 스팟 단위 기술적 실패. 내부 메시지는 응답하지 않는다."""


class CongestionAnalyzer(Protocol):
    def analyze(
        self, spot: CongestionSpotInput, as_of: datetime, window: timedelta
    ) -> CongestionResult: ...


class FakeCongestionAnalyzer:
    """테스트에서만 사용하는 고정 예시 분석기."""

    def analyze(
        self, spot: CongestionSpotInput, as_of: datetime, window: timedelta
    ) -> CongestionResult:
        return CongestionResult(
            congestion_level="MEDIUM",
            report_count=1,
            causes=[CongestionCause(cause_code="EVENT", report_count=1)],
        )


class LLMCongestionAnalyzer:
    """명시 창으로 선별·분류한 스팟을 한 번 추출하고 코드로 집계한다."""

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

    def analyze(
        self, spot: CongestionSpotInput, as_of: datetime, window: timedelta
    ) -> CongestionResult:
        prepared = prepare_spot(spot, as_of, window)
        if not prepared.candidates:
            return aggregate_signals([], as_of, window=window)
        try:
            score, policy = self._configured_scorer()
            classified = classify_spot(prepared, score, policy)
            assessed = assess_spot(build_spot_input(classified), self._complete)
        except (ModelNotReady, UpstreamError, ModelOutputInvalid, RelevanceOutputInvalid) as exc:
            raise AnalysisError("congestion analysis failed") from exc
        return aggregate_signals(assessed.signals, as_of, window=window)

    def _configured_scorer(self) -> tuple[RelevanceScorer, RelevancePolicy]:
        if self._score is not None and self._policy is not None:
            return self._score, self._policy
        try:
            policy = RelevancePolicy(
                exclude_below=float(os.environ["CONGESTION_RELEVANCE_EXCLUDE_BELOW"]),
                related_at_or_above=float(os.environ["CONGESTION_RELEVANCE_RELATED_AT_OR_ABOVE"]),
            )
            score = DecisionsRelevanceScorer(
                timeout=float(os.environ["CONGESTION_DECISIONS_TIMEOUT_SECONDS"])
            )
        except (KeyError, ValueError) as exc:
            raise ModelNotReady("congestion relevance configuration is not ready") from exc
        return score, policy


def get_analyzer() -> CongestionAnalyzer:
    return LLMCongestionAnalyzer()


def analyze_request(
    request: CongestionAnalysisRequest, analyzer: CongestionAnalyzer
) -> CongestionAnalysisResponse:
    def analyze_spot(spot: CongestionSpotInput) -> CongestionSpotResult:
        if not spot.post_bundles:
            result = CongestionResult(congestion_level="UNKNOWN", report_count=0, causes=[])
        else:
            try:
                result = analyzer.analyze(
                    spot, request.as_of, timedelta(minutes=request.window_minutes)
                )
            except AnalysisError:
                return CongestionSpotFailure(spot_id=spot.spot_id)
        return CongestionSpotSuccess(
            spot_id=spot.spot_id,
            result=CongestionResultWithSummary(
                congestion_level=result.congestion_level,
                report_count=result.report_count,
                causes=result.causes,
                summary=summarize_result(result),
            ),
        )

    executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_SPOTS)
    pending: dict[Future[CongestionSpotResult], int] = {}
    results: dict[int, CongestionSpotResult] = {}
    next_index = 0
    complete = False
    try:
        while next_index < len(request.spots) and len(pending) < MAX_CONCURRENT_SPOTS:
            pending[executor.submit(analyze_spot, request.spots[next_index])] = next_index
            next_index += 1
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                results[pending.pop(future)] = future.result()
            while next_index < len(request.spots) and len(pending) < MAX_CONCURRENT_SPOTS:
                pending[executor.submit(analyze_spot, request.spots[next_index])] = next_index
                next_index += 1
        complete = True
    finally:
        executor.shutdown(wait=complete, cancel_futures=not complete)
    spots = [results[index] for index in range(len(request.spots))]
    return CongestionAnalysisResponse(request_id=request.request_id, spots=spots)
