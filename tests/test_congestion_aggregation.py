"""이미 판정된 제보의 시간 선별과 스팟 집계 규칙."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import count

import pytest

from app.features.congestion import aggregator, preprocessor
from app.features.congestion.models import (
    CongestionPostBundleInput,
    CongestionSpotInput,
)

AS_OF = datetime(2026, 10, 7, 6, 0, tzinfo=timezone.utc)
CUTOFF = AS_OF - timedelta(minutes=20)


_IDS = count(1)


@dataclass(frozen=True)
class Judgment:
    author_key: str
    created_at: datetime
    congestion_level: str | None = None
    causes: frozenset[str] = frozenset()
    signal_type: str = "post"
    # 만든 순서대로 커지는 ID. 같은 시각 순서를 시험할 때만 직접 지정한다.
    signal_id: int = field(default_factory=lambda: next(_IDS))


def aggregate(*signals: Judgment) -> dict[str, object]:
    aggregate_signals = getattr(aggregator, "aggregate_signals", None)
    assert callable(aggregate_signals), "the aggregation function is missing"
    return aggregate_signals(signals, AS_OF).model_dump()


def select(spot: CongestionSpotInput) -> list[CongestionPostBundleInput]:
    select_bundles = getattr(preprocessor, "select_analyzable_bundles", None)
    assert callable(select_bundles), "the bundle selection function is missing"
    return select_bundles(spot, AS_OF)


def bundle(
    post_id: int, created_at: datetime, comment_times: list[datetime]
) -> CongestionPostBundleInput:
    return CongestionPostBundleInput.model_validate(
        {
            "post_id": post_id,
            "title": "현장 상황",
            "content": "게시글",
            "author_key": f"author-{post_id}",
            "created_at": created_at.isoformat(),
            "comments": [
                {
                    "comment_id": post_id * 10 + i,
                    "content": "댓글",
                    "author_key": f"commenter-{i}",
                    "created_at": comment_time.isoformat(),
                }
                for i, comment_time in enumerate(comment_times)
            ],
        }
    )


def test_window_has_open_lower_and_closed_upper_boundary() -> None:
    assert aggregate(
        Judgment("too-old", CUTOFF, "HIGH"),
        Judgment("inside", CUTOFF + timedelta(seconds=1), "MEDIUM"),
        Judgment("at-as-of", AS_OF, "MEDIUM"),
    ) == {
        "congestion_level": "MEDIUM",
        "report_count": 2,
        "causes": [{"cause_code": "UNKNOWN", "report_count": 0}],
    }


@pytest.mark.parametrize(
    ("minutes", "expected_count"), [(10, 0), (60, 1)]
)
def test_explicit_window_controls_aggregation(minutes: int, expected_count: int) -> None:
    result = aggregator.aggregate_signals(
        [Judgment("off-peak", AS_OF - timedelta(minutes=45), "MEDIUM")],
        AS_OF,
        window=timedelta(minutes=minutes),
    )
    assert result.report_count == expected_count
    assert result.congestion_level == ("MEDIUM" if expected_count else "UNKNOWN")


@pytest.mark.parametrize("minutes", [0, -1])
def test_aggregation_rejects_nonpositive_window(minutes: int) -> None:
    with pytest.raises(ValueError, match="window"):
        aggregator.aggregate_signals([], AS_OF, window=timedelta(minutes=minutes))


def test_stage_tie_returns_unknown_and_keeps_voter_count() -> None:
    assert aggregate(
        Judgment("a", AS_OF, "LOW"), Judgment("b", AS_OF, "HIGH")
    ) == {
        "congestion_level": "UNKNOWN",
        "report_count": 2,
        "causes": [{"cause_code": "UNKNOWN", "report_count": 0}],
    }


@pytest.mark.parametrize("signals", [(), (Judgment("a", AS_OF),)])
def test_no_valid_stage_or_cause_has_empty_causes(signals: tuple[Judgment, ...]) -> None:
    assert aggregate(*signals) == {
        "congestion_level": "UNKNOWN",
        "report_count": 0,
        "causes": [],
    }


def test_cause_only_report_does_not_vote_on_stage() -> None:
    assert aggregate(Judgment("a", AS_OF, causes=frozenset({"EVENT"}))) == {
        "congestion_level": "UNKNOWN",
        "report_count": 0,
        "causes": [{"cause_code": "EVENT", "report_count": 1}],
    }


def test_latest_stage_and_cause_are_selected_independently() -> None:
    assert aggregate(
        Judgment("a", AS_OF - timedelta(minutes=4), "HIGH", frozenset({"EVENT"})),
        Judgment("a", AS_OF - timedelta(minutes=3), "LOW"),
        Judgment("a", AS_OF - timedelta(minutes=2), causes=frozenset({"DELAY"})),
        Judgment("b", AS_OF, "LOW", frozenset({"EVENT"})),
    ) == {
        "congestion_level": "LOW",
        "report_count": 2,
        "causes": [
            {"cause_code": "DELAY", "report_count": 1},
            {"cause_code": "EVENT", "report_count": 1},
        ],
    }


def test_no_new_concrete_cause_does_not_clear_earlier_cause() -> None:
    assert aggregate(
        Judgment("a", AS_OF - timedelta(minutes=2), causes=frozenset({"EVENT"})),
        Judgment("a", AS_OF - timedelta(minutes=1)),
    ) == {
        "congestion_level": "UNKNOWN",
        "report_count": 0,
        "causes": [{"cause_code": "EVENT", "report_count": 1}],
    }


def test_latest_valid_report_without_cause_drops_earlier_cause() -> None:
    """최신 유효 단계 제보에 원인이 없으면 같은 작성자의 앞선 원인을 버린다(U12)."""
    assert aggregate(
        Judgment("a", AS_OF - timedelta(minutes=2), "MEDIUM", frozenset({"BREAKDOWN"})),
        Judgment("a", AS_OF - timedelta(minutes=1), "LOW"),
    ) == {
        "congestion_level": "LOW",
        "report_count": 1,
        "causes": [{"cause_code": "UNKNOWN", "report_count": 0}],
    }


def test_same_timestamp_prefers_larger_id_within_same_type() -> None:
    """같은 시각의 같은 종류 신호는 ID가 큰 쪽이 최신이다(U11)."""
    assert aggregate(
        Judgment("a", AS_OF, "HIGH", frozenset({"EVENT"}), "post", 1),
        Judgment("a", AS_OF, "LOW", frozenset({"DELAY"}), "post", 2),
        Judgment("b", AS_OF, "MEDIUM", frozenset({"EVENT"}), "post", 3),
    ) == {
        "congestion_level": "UNKNOWN",
        "report_count": 2,
        "causes": [
            {"cause_code": "DELAY", "report_count": 1},
            {"cause_code": "EVENT", "report_count": 1},
        ],
    }


def test_same_timestamp_comment_is_newer_than_post() -> None:
    """원글과 댓글은 ID 번호열이 달라 크기를 비교하지 않고 댓글을 최신으로 본다(U11)."""
    assert aggregate(
        Judgment("a", AS_OF, "HIGH", frozenset({"EVENT"}), "post", 900),
        Judgment("a", AS_OF, "LOW", signal_type="comment", signal_id=5),
    ) == {
        "congestion_level": "LOW",
        "report_count": 1,
        "causes": [{"cause_code": "UNKNOWN", "report_count": 0}],
    }


def test_duplicate_signal_with_conflicting_values_is_excluded() -> None:
    """종류·ID까지 같은 중복 신호의 판정이 다르면 그 작성자를 집계에서 뺀다."""
    assert aggregate(
        Judgment("a", AS_OF, "HIGH", signal_id=7),
        Judgment("a", AS_OF, "LOW", signal_id=7),
    ) == {
        "congestion_level": "UNKNOWN",
        "report_count": 0,
        "causes": [],
    }


def test_same_timestamp_identical_votes_count_author_once() -> None:
    signal = Judgment("a", AS_OF, "LOW", frozenset({"EVENT"}))
    assert aggregate(signal, signal) == {
        "congestion_level": "LOW",
        "report_count": 1,
        "causes": [{"cause_code": "EVENT", "report_count": 1}],
    }


def test_trace_keeps_original_source_indices_after_window_filtering() -> None:
    trace_aggregate = getattr(aggregator, "aggregate_with_trace", None)
    assert callable(trace_aggregate)
    trace = trace_aggregate(
        [
            Judgment("expired", CUTOFF, "HIGH"),
            Judgment("a", AS_OF - timedelta(minutes=3), "HIGH", frozenset({"EVENT"})),
            Judgment("a", AS_OF, "LOW"),
        ],
        AS_OF,
    )

    assert trace.stage_votes[0].signal_indices == (2,)
    # 최신 유효 제보(인덱스 2)에 원인이 없으므로 원인 선택도 인덱스 2다(U12).
    assert trace.cause_votes[0].signal_indices == (2,)
    assert trace.cause_votes[0].value == frozenset()
    assert trace.stage_counts == {"LOW": 1, "MEDIUM": 0, "HIGH": 0}
    assert trace.result.report_count == 1


def test_each_cause_counts_distinct_authors() -> None:
    assert aggregate(
        Judgment("a", AS_OF, "HIGH", frozenset({"EVENT", "ROAD_TRAFFIC"})),
        Judgment("b", AS_OF, "HIGH", frozenset({"EVENT"})),
    ) == {
        "congestion_level": "HIGH",
        "report_count": 2,
        "causes": [
            {"cause_code": "EVENT", "report_count": 2},
            {"cause_code": "ROAD_TRAFFIC", "report_count": 1},
        ],
    }


@pytest.mark.parametrize(
    "signal",
    [
        Judgment("a", AS_OF, "UNKNOWN"),
        Judgment("a", AS_OF, causes=frozenset({"UNKNOWN"})),
    ],
)
def test_unknown_is_a_result_fallback_not_an_individual_vote(signal: Judgment) -> None:
    with pytest.raises(ValueError, match="UNKNOWN"):
        aggregate(signal)


def test_old_parent_without_current_comment_is_not_analyzed() -> None:
    old = bundle(1, CUTOFF, [CUTOFF])
    assert select(CongestionSpotInput(spot_id=1, post_bundles=[old])) == []


def test_old_parent_with_current_comment_keeps_full_comment_context() -> None:
    old = bundle(1, CUTOFF, [CUTOFF, AS_OF])
    selected = select(CongestionSpotInput(spot_id=1, post_bundles=[old]))
    assert selected == [old]
    assert [comment.comment_id for comment in selected[0].comments] == [10, 11]


def test_current_parent_without_comments_is_analyzed() -> None:
    recent = bundle(1, CUTOFF + timedelta(seconds=1), [])
    assert select(CongestionSpotInput(spot_id=1, post_bundles=[recent])) == [recent]
