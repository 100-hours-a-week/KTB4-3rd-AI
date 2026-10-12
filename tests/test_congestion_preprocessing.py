"""스팟 단위 전처리의 시간 경계와 분류 후보·부모 문맥 보존을 검증한다."""

from datetime import datetime, timedelta

import pytest

from app.features.congestion import preprocessor
from app.features.congestion.models import CongestionSpotInput

AS_OF = datetime.fromisoformat("2026-10-12T12:00:00+09:00")


@pytest.fixture
def spot() -> CongestionSpotInput:
    return CongestionSpotInput.model_validate(
        {
            "spot_id": 7,
            "post_bundles": [
                {
                    "post_id": 1,
                    "title": "오래된 원글",
                    "content": "이곳 현장 상황을 알려주세요.",
                    "author_key": "parent",
                    "created_at": "2026-10-12T10:00:00+09:00",
                    "comments": [
                        {
                            "comment_id": 1,
                            "content": "10분 창의 시작 경계",
                            "author_key": "at-ten-minute-cutoff",
                            "created_at": "2026-10-12T11:50:00+09:00",
                        },
                        {
                            "comment_id": 2,
                            "content": "지금 행사 때문에 사람이 많아요.",
                            "author_key": "repeat",
                            "created_at": "2026-10-12T11:50:00.000001+09:00",
                        },
                        {
                            "comment_id": 3,
                            "content": "기준 시각의 댓글",
                            "author_key": "at-as-of",
                            "created_at": "2026-10-12T12:00:00+09:00",
                        },
                        {
                            "comment_id": 4,
                            "content": "60분 창의 시작 경계",
                            "author_key": "at-sixty-minute-cutoff",
                            "created_at": "2026-10-12T11:00:00+09:00",
                        },
                    ],
                },
                {
                    "post_id": 2,
                    "title": "만료된 묶음",
                    "content": "60분 창의 시작 경계에 작성한 원글",
                    "author_key": "expired",
                    "created_at": "2026-10-12T11:00:00+09:00",
                    "comments": [],
                },
                {
                    "post_id": 3,
                    "title": "최근 현장 제보",
                    "content": "지금은 한산해요.",
                    "author_key": "repeat",
                    "created_at": "2026-10-12T11:55:00+09:00",
                    "comments": [],
                },
                {
                    "post_id": 4,
                    "title": "비출퇴근 창의 제보",
                    "content": "지금 줄이 길어요.",
                    "author_key": "off-peak",
                    "created_at": "2026-10-12T11:00:00.000001+09:00",
                    "comments": [],
                },
                {
                    "post_id": 5,
                    "title": "일반 글",
                    "content": "오늘 점심 메뉴 추천해주세요.",
                    "author_key": "unclassified",
                    "created_at": "2026-10-12T12:00:00+09:00",
                    "comments": [],
                },
            ],
        }
    )


@pytest.mark.parametrize(
    ("minutes", "expected_keys", "expected_context_ids"),
    [
        (10, [("comment", 2), ("comment", 3), ("post", 3), ("post", 5)], [1, 3, 5]),
        (
            60,
            [("comment", 1), ("comment", 2), ("comment", 3), ("post", 3), ("post", 4), ("post", 5)],
            [1, 3, 4, 5],
        ),
    ],
)
def test_spot_candidates_use_requested_window_and_keep_input_order(
    spot: CongestionSpotInput,
    minutes: int,
    expected_keys: list[tuple[str, int]],
    expected_context_ids: list[int],
) -> None:
    prepared = preprocessor.prepare_spot(spot, AS_OF, timedelta(minutes=minutes))

    assert [(candidate.signal_type, candidate.signal_id) for candidate in prepared.candidates] == (
        expected_keys
    )
    assert [post.post_id for post in prepared.post_bundles] == expected_context_ids


def test_comment_candidate_keeps_parent_and_original_metadata(spot: CongestionSpotInput) -> None:
    original = spot.model_dump()
    prepared = preprocessor.prepare_spot(spot, AS_OF, timedelta(minutes=10))
    comment = prepared.candidates[0]

    assert (comment.signal_type, comment.signal_id, comment.post_id) == ("comment", 2, 1)
    assert comment.title is None
    assert comment.content == "지금 행사 때문에 사람이 많아요."
    assert comment.author_key == "repeat"
    assert comment.created_at.isoformat() == "2026-10-12T11:50:00.000001+09:00"
    assert prepared.post_bundles[0].title == "오래된 원글"
    assert [item.comment_id for item in prepared.post_bundles[0].comments] == [1, 2, 3, 4]
    assert spot.model_dump() == original


def test_post_and_comment_with_same_numeric_id_remain_separate_candidates(
    spot: CongestionSpotInput,
) -> None:
    prepared = preprocessor.prepare_spot(spot, AS_OF, timedelta(minutes=10))

    assert [
        candidate.signal_type for candidate in prepared.candidates if candidate.signal_id == 3
    ] == ["comment", "post"]
    assert prepared.candidates[2].title == "최근 현장 제보"


def test_repeated_authors_are_kept_for_individual_judgment(spot: CongestionSpotInput) -> None:
    prepared = preprocessor.prepare_spot(spot, AS_OF, timedelta(minutes=10))

    assert [
        candidate.signal_id for candidate in prepared.candidates if candidate.author_key == "repeat"
    ] == [2, 3]


@pytest.mark.parametrize("only_expired", [False, True])
def test_spot_without_current_signals_has_no_candidates_or_context(
    spot: CongestionSpotInput, only_expired: bool
) -> None:
    empty = CongestionSpotInput(
        spot_id=7, post_bundles=[spot.post_bundles[1]] if only_expired else []
    )
    prepared = preprocessor.prepare_spot(empty, AS_OF, timedelta(minutes=60))

    assert prepared.candidates == ()
    assert prepared.post_bundles == ()


def test_equivalent_timezone_offsets_select_the_same_signals(spot: CongestionSpotInput) -> None:
    as_of_utc = datetime.fromisoformat("2026-10-12T03:00:00+00:00")
    prepared = preprocessor.prepare_spot(spot, as_of_utc, timedelta(minutes=10))

    assert [(candidate.signal_type, candidate.signal_id) for candidate in prepared.candidates] == [
        ("comment", 2),
        ("comment", 3),
        ("post", 3),
        ("post", 5),
    ]


@pytest.mark.parametrize("minutes", [0, -1])
def test_nonpositive_window_is_rejected(spot: CongestionSpotInput, minutes: int) -> None:
    with pytest.raises(ValueError, match="window"):
        preprocessor.prepare_spot(spot, AS_OF, timedelta(minutes=minutes))
