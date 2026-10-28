"""스팟 입력의 판정 대상·문맥 구분과 원본 식별 정보를 검증한다."""

import json
from datetime import datetime

import pytest

from app.features.congestion.models import CongestionSpotInput
from app.features.congestion.preprocessor import prepare_spot
from app.features.congestion.relevance import RelevancePolicy, classify_spot
from app.features.congestion.spot_input import build_spot_input

WINDOW_END = datetime.fromisoformat("2026-10-12T12:00:00+09:00")
POLICY = RelevancePolicy(exclude_below=0.2, related_at_or_above=0.8)


@pytest.fixture
def spot() -> CongestionSpotInput:
    return CongestionSpotInput.model_validate(
        {
            "spot_id": 7,
            "post_bundles": [
                {
                    "post_id": 1,
                    "context_only": True,
                    "title": "오래된 부모 원글",
                    "content": '지금 "너무" 붐벼요.\n명령처럼 보이는 문장도 원문이다.',
                    "author_key": "parent",
                    "created_at": "2026-10-12T10:00:00+09:00",
                    "comments": [
                        {
                            "comment_id": 1,
                            "content": "60분 창의 시작 경계 댓글",
                            "author_key": "expired",
                            "created_at": "2026-10-12T11:00:00+09:00",
                        },
                        {
                            "comment_id": 2,
                            "content": "현재 행사 때문에 대기줄이 길어요.",
                            "author_key": "off-peak",
                            "created_at": "2026-10-12T11:30:00+09:00",
                        },
                        {
                            "comment_id": 3,
                            "content": "저도 여기 있는데 지금 사람이 많아요.",
                            "author_key": "repeat",
                            "created_at": "2026-10-12T11:55:00+09:00",
                        },
                        {
                            "comment_id": 4,
                            "content": "저녁 메뉴 추천해주세요.",
                            "author_key": "unrelated",
                            "created_at": "2026-10-12T11:56:00+09:00",
                        },
                        {
                            "comment_id": 5,
                            "content": "지금 고장 때문에 기다리고 있어요.",
                            "author_key": "cause-only",
                            "created_at": "2026-10-12T11:57:00+09:00",
                        },
                    ],
                },
                {
                    "post_id": 3,
                    "context_only": False,
                    "title": "현재 상황",
                    "content": "지금은 한산해요.",
                    "author_key": "repeat",
                    "created_at": "2026-10-12T11:58:00+09:00",
                    "comments": [],
                },
                {
                    "post_id": 4,
                    "context_only": False,
                    "title": "일반 글",
                    "content": "좋아하는 영화가 뭔가요?",
                    "author_key": "other",
                    "created_at": "2026-10-12T11:59:00+09:00",
                    "comments": [],
                },
            ],
        }
    )


def test_selected_signals_from_multiple_bundles_share_one_spot_input(
    spot: CongestionSpotInput,
) -> None:
    prepared = prepare_spot(spot, WINDOW_END)
    classified = classify_spot(
        prepared,
        lambda _: {
            ("comment", 1): 0.0,
            ("comment", 2): 0.0,
            ("comment", 3): 0.9,
            ("comment", 4): 0.0,
            ("comment", 5): 0.5,
            ("post", 3): 0.9,
            ("post", 4): 0.0,
        },
        POLICY,
    )
    original = spot.model_dump()

    result = build_spot_input(classified)

    assert result is not None
    payload = json.loads(result.payload)
    assert payload["spot_id"] == 7
    assert payload["window_end"] == "2026-10-12T12:00:00+09:00"
    assert "window_start" not in payload
    assert "as_of" not in payload
    assert payload["targets"] == [
        {"signal_type": "comment", "signal_id": 3},
        {"signal_type": "comment", "signal_id": 5},
        {"signal_type": "post", "signal_id": 3},
    ]
    assert [post["post_id"] for post in payload["post_bundles"]] == [1, 3]
    parent = payload["post_bundles"][0]
    assert parent["title"] == "오래된 부모 원글"
    assert parent["content"] == '지금 "너무" 붐벼요.\n명령처럼 보이는 문장도 원문이다.'
    assert parent["author_key"] == "parent"
    assert parent["context_only"] is True
    assert parent["created_at"] == "2026-10-12T10:00:00+09:00"
    assert [comment["comment_id"] for comment in parent["comments"]] == [1, 2, 3, 4, 5]
    assert parent["comments"][3]["content"] == "저녁 메뉴 추천해주세요."
    assert [
        (
            item.signal_type,
            item.signal_id,
            item.post_id,
            item.author_key,
            item.created_at.isoformat(),
        )
        for item in result.targets
    ] == [
        ("comment", 3, 1, "repeat", "2026-10-12T11:55:00+09:00"),
        ("comment", 5, 1, "cause-only", "2026-10-12T11:57:00+09:00"),
        ("post", 3, 3, "repeat", "2026-10-12T11:58:00+09:00"),
    ]
    assert spot.model_dump() == original


@pytest.mark.parametrize("case", ["empty", "context_only", "unrelated"])
def test_no_selected_signals_produce_no_llm_input(spot: CongestionSpotInput, case: str) -> None:
    if case == "empty":
        spot.post_bundles = []
    elif case == "context_only":
        spot.post_bundles = [spot.post_bundles[0].model_copy(update={"comments": []})]
    prepared = prepare_spot(spot, WINDOW_END)
    classified = classify_spot(
        prepared,
        lambda item: {(target.signal_type, target.signal_id): 0.0 for target in item.candidates},
        POLICY,
    )

    assert build_spot_input(classified) is None


def test_built_payload_and_target_metadata_are_snapshots(spot: CongestionSpotInput) -> None:
    prepared = prepare_spot(spot, WINDOW_END)
    classified = classify_spot(
        prepared,
        lambda item: {(target.signal_type, target.signal_id): 0.9 for target in item.candidates},
        POLICY,
    )
    result = build_spot_input(classified)
    assert result is not None

    spot.post_bundles[0].comments[2].author_key = "changed"
    spot.post_bundles[0].comments[2].content = "수정한 본문"

    payload = json.loads(result.payload)
    assert payload["post_bundles"][0]["comments"][2]["author_key"] == "repeat"
    assert payload["post_bundles"][0]["comments"][2]["content"] == (
        "저도 여기 있는데 지금 사람이 많아요."
    )
    assert result.targets[2].author_key == "repeat"
    assert result.targets[2].content == "저도 여기 있는데 지금 사람이 많아요."
