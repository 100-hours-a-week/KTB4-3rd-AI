"""스팟별 분석기 경계와 요청 결과 조립."""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from ...core.exceptions import ModelNotReady, UpstreamError
from .aggregator import aggregate_signals
from .extractor import ModelOutputInvalid, assess_bundle, call_api_model
from .models import (
    CongestionAnalysisRequest,
    CongestionAnalysisResponse,
    CongestionCause,
    CongestionResult,
    CongestionSpotFailure,
    CongestionSpotInput,
    CongestionSpotResult,
    CongestionSpotSuccess,
)
from .preprocessor import select_analyzable_bundles


class AnalysisError(Exception):
    """분석기가 보고하는 스팟 단위 기술적 실패. 내부 메시지는 응답하지 않는다."""


class CongestionAnalyzer(Protocol):
    def analyze(
        self, spot: CongestionSpotInput, as_of: datetime
    ) -> CongestionResult: ...


class FakeCongestionAnalyzer:
    """테스트에서만 사용하는 고정 예시 분석기."""

    def analyze(self, spot: CongestionSpotInput, as_of: datetime) -> CongestionResult:
        return CongestionResult(
            congestion_level="MEDIUM",
            report_count=1,
            causes=[CongestionCause(cause_code="EVENT", report_count=1)],
        )


class LLMCongestionAnalyzer:
    """게시글 묶음별 순차 판정을 독립 집계 함수에 연결한다."""

    def __init__(self, complete: Callable[[str], str] = call_api_model) -> None:
        self._complete = complete

    def analyze(self, spot: CongestionSpotInput, as_of: datetime) -> CongestionResult:
        signals = []
        for bundle in select_analyzable_bundles(spot, as_of):
            try:
                signals.extend(assess_bundle(bundle, as_of, self._complete))
            except (ModelNotReady, UpstreamError, ModelOutputInvalid) as exc:
                raise AnalysisError("congestion analysis failed") from exc
        return aggregate_signals(signals, as_of)


def get_analyzer() -> CongestionAnalyzer:
    return LLMCongestionAnalyzer()


def analyze_request(
    request: CongestionAnalysisRequest, analyzer: CongestionAnalyzer
) -> CongestionAnalysisResponse:
    spots: list[CongestionSpotResult] = []
    for spot in request.spots:
        if not spot.post_bundles:
            result = CongestionResult(
                congestion_level="UNKNOWN", report_count=0, causes=[]
            )
        else:
            try:
                result = analyzer.analyze(spot, request.as_of)
            except AnalysisError:
                spots.append(CongestionSpotFailure(spot_id=spot.spot_id))
                continue
        spots.append(CongestionSpotSuccess(spot_id=spot.spot_id, result=result))
    return CongestionAnalysisResponse(request_id=request.request_id, spots=spots)
