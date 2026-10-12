"""한 스팟의 내부 흐름을 눈으로 확인하는 개발용 명령."""

import argparse
import json
import sys
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from pydantic import Field

from ...core.exceptions import AppError
from .aggregator import aggregate_signals
from .decisions import DecisionsRelevanceScorer
from .extractor import call_api_model
from .models import ApiModel, CongestionAnalysisRequest, PositiveId
from .preprocessor import PreparedSpot, prepare_spot
from .relevance import RelevancePolicy, RelevanceScorer, SignalKey, classify_spot
from .spot_extractor import assess_spot
from .spot_input import build_spot_input
from .summarizer import summarize_result


class PreviewScore(ApiModel):
    signal_type: Literal["post", "comment"]
    signal_id: PositiveId
    probability: float = Field(strict=True, ge=0, le=1, allow_inf_nan=False)


class PreviewCase(ApiModel):
    """요청과 모의 모델 응답을 함께 담으며 외부 API 계약과 구분한다."""

    request: CongestionAnalysisRequest
    relevance_policy: RelevancePolicy
    scores: list[PreviewScore]
    model_output: dict[str, Any]


def preview_spot(
    case: PreviewCase,
    score: RelevanceScorer,
    complete: Callable[[str], str],
    policy: RelevancePolicy,
) -> dict[str, Any]:
    """실제 전처리·분류·검증·집계 함수를 실행하고 중간값을 노출한다."""
    if len(case.request.spots) != 1:
        raise ValueError("preview requires exactly one spot")
    window = timedelta(minutes=case.request.window_minutes)
    started = perf_counter()
    prepared = prepare_spot(case.request.spots[0], case.request.as_of, window)
    classified = classify_spot(prepared, score, policy)
    model_input = build_spot_input(classified)
    raw_outputs: list[str] = []

    def record(prompt: str) -> str:
        raw = complete(prompt)
        raw_outputs.append(raw)
        return raw

    assessed = assess_spot(model_input, record)
    aggregate = aggregate_signals(assessed.signals, case.request.as_of, window=window)
    return {
        "spot_id": prepared.spot_id,
        "window_minutes": case.request.window_minutes,
        "relevance_policy": {
            "exclude_below": policy.exclude_below,
            "related_at_or_above": policy.related_at_or_above,
        },
        "relevance": [
            {
                "signal_type": item.candidate.signal_type,
                "signal_id": item.candidate.signal_id,
                "probability": item.probability,
                "status": item.status,
            }
            for item in classified.decisions
        ],
        "llm_input": json.loads(model_input.payload) if model_input else None,
        "extraction_calls": len(raw_outputs),
        "model_output": json.loads(raw_outputs[0]) if raw_outputs else None,
        "judgments": [
            {
                "signal_type": target.signal_type,
                "signal_id": target.signal_id,
                "author_key": signal.author_key,
                "created_at": signal.created_at.isoformat(),
                "congestion_level": signal.congestion_level,
                "causes": sorted(signal.causes),
            }
            for target, signal in zip(
                model_input.targets if model_input else (), assessed.signals, strict=True
            )
        ],
        "aggregate": aggregate.model_dump(),
        "summary": summarize_result(aggregate),
        "elapsed_seconds": round(perf_counter() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="혼잡도 스팟 분석 수동 미리보기")
    parser.add_argument("--case", type=Path, required=True, help="요청·모의 응답 JSON 파일")
    parser.add_argument("--live", action="store_true", help="모의 응답 대신 실제 모델 호출")
    parser.add_argument("--timeout", type=float, help="실제 Decisions 동작별 타임아웃(초)")
    parser.add_argument("--exclude-below", type=float, help="실제 실행에 쓸 제외 임계값")
    parser.add_argument("--related-at-or-above", type=float, help="실제 실행에 쓸 관련 임계값")
    args = parser.parse_args()
    if args.live and any(
        value is None for value in (args.timeout, args.exclude_below, args.related_at_or_above)
    ):
        parser.error("--live에는 --timeout, --exclude-below, --related-at-or-above가 필요합니다")
    try:
        case = PreviewCase.model_validate_json(args.case.read_text(encoding="utf-8"))
        policy = case.relevance_policy
        score: RelevanceScorer
        complete: Callable[[str], str]
        if args.live:
            policy = RelevancePolicy(args.exclude_below, args.related_at_or_above)
            score = DecisionsRelevanceScorer(timeout=args.timeout)
            complete = call_api_model
        else:
            scores: dict[SignalKey, float] = {
                (item.signal_type, item.signal_id): item.probability for item in case.scores
            }
            if len(scores) != len(case.scores):
                raise ValueError("duplicate preview score ID")

            def mock_score(_prepared: PreparedSpot) -> dict[SignalKey, float]:
                return scores

            score = mock_score

            def mock_complete(_prompt: str) -> str:
                return json.dumps(case.model_output, ensure_ascii=False)

            complete = mock_complete
        result = preview_spot(case, score, complete, policy)
    except (AppError, ValueError, OSError) as exc:
        print(f"미리보기 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    result["mode"] = "live" if args.live else "mock"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
