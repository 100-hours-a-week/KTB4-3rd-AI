"""스팟별 분석기 경계와 요청 결과 조립."""

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
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

MAX_CONCURRENT_SPOTS = 3


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
    def analyze_spot(spot: CongestionSpotInput) -> CongestionSpotResult:
        if not spot.post_bundles:
            result = CongestionResult(
                congestion_level="UNKNOWN", report_count=0, causes=[]
            )
        else:
            try:
                result = analyzer.analyze(spot, request.as_of)
            except AnalysisError:
                return CongestionSpotFailure(spot_id=spot.spot_id)
        return CongestionSpotSuccess(spot_id=spot.spot_id, result=result)

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
