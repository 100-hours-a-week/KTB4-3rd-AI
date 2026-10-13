"""부모 원인의 자동 복사와 댓글 자체의 원인 보존을 실제 스팟 추출로 평가한다."""

import json
import os
from datetime import timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest

from app.features.congestion.aggregator import aggregate_signals
from app.features.congestion.extractor import call_api_model
from app.features.congestion.models import CongestionAnalysisRequest
from app.features.congestion.preprocessor import prepare_spot
from app.features.congestion.relevance import RelevancePolicy, SignalKey, classify_spot
from app.features.congestion.spot_extractor import assess_spot
from app.features.congestion.spot_input import build_spot_input

CASES = json.loads(
    (
        Path(__file__).parent / "fixtures/congestion/parent-cause-regression.json"
    ).read_text()
)
pytestmark = pytest.mark.skipif(
    os.environ.get("CONGESTION_LIVE_EVAL") != "1",
    reason="set CONGESTION_LIVE_EVAL=1 explicitly to call the OpenAI API",
)


@pytest.mark.parametrize(
    ("case", "repeat"),
    [
        pytest.param(case, repeat, id=f"{case['id']}-{repeat + 1}")
        for case in CASES
        for repeat in range(case["repeats"])
    ],
)
def test_live_spot_preserves_each_signals_own_cause(
    case: dict[str, Any], repeat: int
) -> None:
    # 분류 결과를 고정하고 같은 targets에서 LLM의 의미 판정만 반복 비교한다.
    request = CongestionAnalysisRequest.model_validate(case["request"])
    window = timedelta(minutes=request.window_minutes)
    prepared = prepare_spot(request.spots[0], request.as_of, window)
    targets = {(item["signal_type"], item["signal_id"]) for item in case["targets"]}
    scores: dict[SignalKey, float] = {
        (item.signal_type, item.signal_id): float(
            (item.signal_type, item.signal_id) in targets
        )
        for item in prepared.candidates
    }
    model_input = build_spot_input(
        classify_spot(prepared, lambda _: scores, RelevancePolicy(0.2, 0.8))
    )
    assert model_input is not None
    started = perf_counter()
    assessed = assess_spot(model_input, call_api_model)
    actual = [
        {
            "signal_type": target.signal_type,
            "signal_id": target.signal_id,
            "congestion_level": signal.congestion_level,
            "causes": sorted(signal.causes),
        }
        for target, signal in zip(model_input.targets, assessed.signals, strict=True)
    ]
    print(
        json.dumps(
            {
                "case": case["id"],
                "repeat": repeat + 1,
                "elapsed_seconds": round(perf_counter() - started, 3),
                "actual": actual,
            },
            ensure_ascii=False,
        )
    )
    assert actual == case["expected_signals"]
    assert (
        aggregate_signals(assessed.signals, request.as_of, window=window).model_dump()
        == case["expected_aggregate"]
    )
