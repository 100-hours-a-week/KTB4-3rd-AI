"""확정 판정 사례의 수동 실모델 평가. 기본 pytest에서는 외부 호출을 생략한다."""

import os
from datetime import datetime, timedelta, timezone
from time import perf_counter

import pytest

from app.features.congestion.extractor import assess_bundle, call_api_model
from app.features.congestion.models import CongestionAnalysisRequest

pytestmark = pytest.mark.skipif(
    os.environ.get("CONGESTION_LIVE_EVAL") != "1",
    reason="set CONGESTION_LIVE_EVAL=1 explicitly to call the OpenAI API",
)

Expected = dict[str, tuple[str | None, frozenset[str]]]


@pytest.mark.parametrize(
    ("content", "comments", "expected"),
    [
        pytest.param(
            "지금 사람이 엄청 많은데 이동은 괜찮아요.",
            [],
            {"post": ("HIGH", frozenset())},
            id="high-despite-mobility",
        ),
        pytest.param(
            "지금 줄이 길어서 이번 버스 못 탈 것 같아요.",
            [],
            {"post": ("MEDIUM", frozenset())},
            id="long-line-predicted-failure",
        ),
        pytest.param(
            "지금 사람은 있지만 여유 있게 이동해요.",
            [],
            {"post": ("LOW", frozenset())},
            id="low-with-room",
        ),
        pytest.param(
            "지금 줄 서 있어요.",
            [],
            {"post": (None, frozenset())},
            id="line-without-severity",
        ),
        pytest.param(
            "10분 전에 봤을 때는 사람이 많았어요.",
            [],
            {"post": (None, frozenset())},
            id="explicit-past-observation",
        ),
        pytest.param(
            "지금 신호기가 고장 나서 승강장에 사람들이 많이 몰려 있어요.",
            [],
            {"post": ("MEDIUM", frozenset({"BREAKDOWN"}))},
            id="breakdown-without-assumed-delay",
        ),
        pytest.param(
            "지금 사람이 엄청 많아요.",
            ["저도요"],
            {
                "post": ("HIGH", frozenset()),
                "comment-1": (None, frozenset()),
            },
            id="bare-agreement-comment",
        ),
        pytest.param(
            "지금 사람이 엄청 많아서 못 움직이겠어요.",
            ["저도 여기 있는데 똑같아요."],
            {
                "post": ("HIGH", frozenset()),
                "comment-1": ("HIGH", frozenset()),
            },
            id="observing-comment-uses-parent",
        ),
        pytest.param(
            "지금 비가 많이 와요.",
            [],
            {"post": (None, frozenset())},
            id="weather-without-congestion-link",
        ),
        pytest.param(
            "지금 폭우 때문에 대기줄이 길어졌어요.",
            [],
            {"post": ("MEDIUM", frozenset({"WEATHER"}))},
            id="weather-causes-long-line",
        ),
        pytest.param(
            "지금 신호기 고장 때문에 사람들이 기다리고 있어요. 얼마나 붐비는지는 모르겠어요.",
            [],
            {"post": (None, frozenset({"BREAKDOWN"}))},
            id="cause-only-breakdown-with-waiting",
        ),
        pytest.param(
            "지금 앞쪽은 사람이 엄청 많고 뒤쪽은 한산해요.",
            [],
            {"post": (None, frozenset())},
            id="simultaneous-conflicting-levels",
        ),
        pytest.param(
            "방금 봤을 때 사람이 많았어요.",
            [],
            {"post": (None, frozenset())},
            id="just-observed-past",
        ),
    ],
)
def test_live_judgment_against_decision_record(
    content: str, comments: list[str], expected: Expected | None, request: pytest.FixtureRequest
) -> None:
    as_of = datetime.now(timezone.utc)
    data = {
        "request_id": "synthetic-quality-eval",
        "as_of": as_of.isoformat(),
        "spots": [
            {
                "spot_id": 1,
                "post_bundles": [
                    {
                        "post_id": 1,
                        "title": "현장 제보",
                        "content": content,
                        "author_key": "post",
                        "created_at": (as_of - timedelta(minutes=1)).isoformat(),
                        "comments": [
                            {
                                "comment_id": index,
                                "content": comment,
                                "author_key": f"comment-{index}",
                                "created_at": (as_of - timedelta(seconds=30)).isoformat(),
                            }
                            for index, comment in enumerate(comments, start=1)
                        ],
                    }
                ],
            }
        ],
    }
    parsed = CongestionAnalysisRequest.model_validate(data)
    started = perf_counter()
    try:
        signals = assess_bundle(parsed.spots[0].post_bundles[0], parsed.as_of, call_api_model)
    finally:
        print(f"{request.node.name} elapsed_seconds={perf_counter() - started:.3f}")
    actual = {
        signal.author_key: (signal.congestion_level, signal.causes) for signal in signals
    }
    if expected is None:
        print(f"{request.node.name} exploratory_result={actual}")
    else:
        assert actual == expected
