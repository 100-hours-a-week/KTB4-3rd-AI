"""관련성 확률에 따른 선별과 후보 ID·문맥 보존을 검증한다."""

from datetime import datetime
from typing import Any

import pytest

from app.features.congestion import relevance
from app.features.congestion.models import CongestionSpotInput
from app.features.congestion.preprocessor import PreparedSpot, prepare_spot


@pytest.fixture
def prepared() -> PreparedSpot:
    spot = CongestionSpotInput.model_validate(
        {
            "spot_id": 1,
            "post_bundles": [
                {
                    "post_id": 1,
                    "context_only": False,
                    "title": "지금 상황",
                    "content": "지금 사람이 많아요.",
                    "author_key": "repeat",
                    "created_at": "2026-10-12T11:55:00+09:00",
                    "comments": [
                        {
                            "comment_id": 1,
                            "content": "저는 여기 있는데 지금 한산해요.",
                            "author_key": "repeat",
                            "created_at": "2026-10-12T11:56:00+09:00",
                        },
                        {
                            "comment_id": 2,
                            "content": "오늘 점심 뭐 먹지?",
                            "author_key": "other",
                            "created_at": "2026-10-12T11:57:00+09:00",
                        },
                    ],
                }
            ],
        }
    )
    return prepare_spot(spot, datetime.fromisoformat("2026-10-12T12:00:00+09:00"))


@pytest.mark.parametrize(
    ("probability", "expected"),
    [
        (0.0, "UNRELATED"),
        (0.199, "UNRELATED"),
        (0.2, "UNCERTAIN"),
        (0.799, "UNCERTAIN"),
        (0.8, "RELATED"),
        (1.0, "RELATED"),
    ],
)
def test_only_probabilities_below_explicit_exclusion_threshold_are_dropped(
    prepared: PreparedSpot, probability: float, expected: str
) -> None:
    policy = relevance.RelevancePolicy(exclude_below=0.2, related_at_or_above=0.8)
    result = relevance.classify_spot(
        prepared,
        lambda _: {("post", 1): probability, ("comment", 1): 0.9, ("comment", 2): 0.0},
        policy,
    )

    assert result.decisions[0].status == expected
    assert result.decisions[0].probability == probability
    assert [(item.signal_type, item.signal_id) for item in result.selected_candidates] == (
        [("comment", 1)] if expected == "UNRELATED" else [("post", 1), ("comment", 1)]
    )


def test_score_order_cannot_reassign_post_and_comment_identity(prepared: PreparedSpot) -> None:
    result = relevance.classify_spot(
        prepared,
        lambda _: {("comment", 2): 0.0, ("comment", 1): 0.5, ("post", 1): 0.9},
        relevance.RelevancePolicy(exclude_below=0.2, related_at_or_above=0.8),
    )

    assert [(item.signal_type, item.signal_id) for item in result.selected_candidates] == [
        ("post", 1),
        ("comment", 1),
    ]
    assert [item.author_key for item in result.selected_candidates] == ["repeat", "repeat"]
    assert result.prepared.post_bundles[0].comments[1].content == "오늘 점심 뭐 먹지?"


@pytest.mark.parametrize(
    "scores",
    [
        {},
        {("post", 1): 0.9, ("comment", 1): 0.5},
        {("post", 1): 0.9, ("comment", 1): 0.5, ("comment", 2): 0.0, ("post", 9): 0.1},
    ],
)
def test_missing_or_extra_score_ids_are_rejected(
    prepared: PreparedSpot, scores: dict[relevance.SignalKey, float]
) -> None:
    with pytest.raises(relevance.RelevanceOutputInvalid):
        relevance.classify_spot(
            prepared,
            lambda _: scores,
            relevance.RelevancePolicy(exclude_below=0.2, related_at_or_above=0.8),
        )


@pytest.mark.parametrize(
    "probability", [-0.01, 1.01, float("nan"), float("inf"), True, "0.1", 10**1000]
)
def test_invalid_score_is_not_treated_as_irrelevant(
    prepared: PreparedSpot, probability: Any
) -> None:
    with pytest.raises(relevance.RelevanceOutputInvalid):
        relevance.classify_spot(
            prepared,
            lambda _: {("post", 1): probability, ("comment", 1): 0.5, ("comment", 2): 0.0},
            relevance.RelevancePolicy(exclude_below=0.2, related_at_or_above=0.8),
        )


@pytest.mark.parametrize(
    ("exclude_below", "related_at_or_above"),
    [
        (-0.1, 0.8),
        (0.2, 1.1),
        (0.8, 0.8),
        (0.9, 0.8),
        (float("nan"), 0.8),
        (0.2, True),
        (10**1000, 0.8),
    ],
)
def test_invalid_thresholds_are_rejected(exclude_below: float, related_at_or_above: float) -> None:
    with pytest.raises(ValueError):
        relevance.RelevancePolicy(
            exclude_below=exclude_below, related_at_or_above=related_at_or_above
        )


def test_empty_spot_skips_scoring() -> None:
    prepared = prepare_spot(
        CongestionSpotInput(spot_id=1, post_bundles=[]),
        datetime.fromisoformat("2026-10-12T12:00:00+09:00"),
    )

    def unexpected(_prepared: PreparedSpot) -> dict[relevance.SignalKey, float]:
        pytest.fail("빈 후보에 대한 분류기가 호출됨")

    result = relevance.classify_spot(
        prepared,
        unexpected,
        relevance.RelevancePolicy(exclude_below=0.2, related_at_or_above=0.8),
    )
    assert result.decisions == ()
    assert result.selected_candidates == ()
