"""스팟별 분석기 경계와 요청 결과 조립."""

from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Protocol
from uuid import uuid4

import httpx

from ...core.exceptions import AppError, CongestionDecisionsFailed, UpstreamError
from ...core.logging import SAFE_ID, get_logger, http_reason, request_id
from .aggregator import aggregate_with_trace
from .config import RELEVANCE_POLICY
from .decisions import DecisionsRelevanceScorer
from .extractor import AssessedSignal, call_api_model
from .models import (
    CongestionAnalysisFailure,
    CongestionAnalysisMessage,
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
        signals: tuple[AssessedSignal, ...] = ()
        if not prepared.candidates:
            logger.info(
                "congestion analysis skipped: spot_id=%s reason=no_candidates",
                message.spot_id,
            )
        else:
            started = monotonic()
            try:
                score, policy = self._configured_scorer()
                classified = classify_spot(prepared, score, policy)
            except (UpstreamError, RelevanceOutputInvalid) as exc:
                cause = exc.__cause__
                reason = (
                    exc.reason
                    if isinstance(exc, RelevanceOutputInvalid)
                    else (
                        http_reason(cause)
                        if isinstance(cause, httpx.HTTPError)
                        else "not_json"
                    )
                )
                logger.warning(
                    "congestion decisions failed: reason=%s status=%s error=%s ms=%s",
                    reason,
                    cause.response.status_code
                    if isinstance(cause, httpx.HTTPStatusError)
                    else "-",
                    type(exc).__name__,
                    int((monotonic() - started) * 1000),
                )
                raise CongestionDecisionsFailed() from exc
            logger.info(
                "congestion relevance done: candidates=%s related=%s uncertain=%s unrelated=%s ms=%s",
                len(prepared.candidates),
                sum(d.status == "RELATED" for d in classified.decisions),
                sum(d.status == "UNCERTAIN" for d in classified.decisions),
                sum(d.status == "UNRELATED" for d in classified.decisions),
                int((monotonic() - started) * 1000),
            )
            model_input = build_spot_input(classified)
            if model_input is None:
                logger.info(
                    "congestion analysis skipped: spot_id=%s reason=all_unrelated",
                    message.spot_id,
                )
            else:
                started = monotonic()
                signals = assess_spot(model_input, self._complete).signals
                logger.info(
                    "congestion judge done: targets=%s signals=%s ms=%s",
                    len(model_input.targets),
                    len(signals),
                    int((monotonic() - started) * 1000),
                )
        trace = aggregate_with_trace(signals)
        result = trace.result
        logger.info(
            "congestion aggregate done: level=%s decision=%s report_count=%s causes=%s",
            result.congestion_level,
            trace.decision,
            result.report_count,
            ",".join(c.cause_code for c in result.causes) or "-",
        )
        return result

    def _configured_scorer(self) -> tuple[RelevanceScorer, RelevancePolicy]:
        if self._score is not None and self._policy is not None:
            return self._score, self._policy
        return DecisionsRelevanceScorer(), RELEVANCE_POLICY


def get_analyzer() -> CongestionAnalyzer:
    return LLMCongestionAnalyzer()


def failure_result(message: CongestionAnalysisMessage) -> CongestionAnalysisFailure:
    """consumer가 예외를 받았을 때 실패 결과를 만든다."""
    return CongestionAnalysisFailure(
        event_id=str(uuid4()),
        schema_version=1,
        window_end=message.window_end,
        spot_id=message.spot_id,
        analyzed_at=datetime.now(UTC),
    )


def analyze_message(
    message: CongestionAnalysisMessage, analyzer: CongestionAnalyzer
) -> CongestionAnalysisSuccess:
    # 공백·줄바꿈 등 로그 형식을 깨는 event_id는 로그에 넣지 않는다(HTTP 미들웨어와 같은 기준).
    event_id = message.event_id if SAFE_ID.match(message.event_id) else None
    token = None
    if not request_id.get():
        token = request_id.set(event_id or uuid4().hex[:12])
    started = monotonic()
    try:
        logger.info(
            "congestion analysis started: spot_id=%s event_id=%s bundles=%s posts=%s "
            "comments=%s context_only=%s",
            message.spot_id,
            event_id or "-",
            len(message.post_bundles),
            sum(not p.context_only for p in message.post_bundles),
            sum(len(p.comments) for p in message.post_bundles),
            sum(p.context_only for p in message.post_bundles),
        )
        result = analyzer.analyze(message)
        response = CongestionAnalysisSuccess(
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
        logger.info(
            "congestion analysis done: spot_id=%s level=%s ms=%s",
            message.spot_id,
            result.congestion_level,
            int((monotonic() - started) * 1000),
        )
        return response
    except Exception as exc:
        # 예상 밖 예외는 원인을 찾을 수 있게 스택 트레이스를 함께 남긴다(Kafka 경로에는 HTTP 처리기가 없다).
        log = logger.warning if isinstance(exc, AppError) else logger.exception
        log(
            "congestion analysis failed: spot_id=%s event_id=%s code=%s ms=%s",
            message.spot_id,
            event_id or "-",
            exc.code if isinstance(exc, AppError) else "INTERNAL",
            int((monotonic() - started) * 1000),
        )
        raise
    finally:
        if token is not None:
            request_id.reset(token)
