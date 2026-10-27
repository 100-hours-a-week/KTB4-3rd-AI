"""이미 판정된 신호의 스팟 집계와 기존 묶음 선정 규칙."""

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
    return aggregate_signals(signals).model_dump()


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
            "context_only": False,
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


@pytest.mark.parametrize(
    ("reports", "level", "report_count", "decision"),
    [
        ([("a1", "LOW", 52, "post"), ("a2", "HIGH", 57, "post")],
         "HIGH", 2, "TIE_LATEST"),
        ([("a1", "HIGH", 50, "post"), ("a2", "HIGH", 51, "post"),
          ("a3", "LOW", 59, "post")], "HIGH", 3, "UNIQUE_TOP"),
        ([("a1", "LOW", 55, "post"), ("a2", "HIGH", 55, "comment")],
         "HIGH", 2, "TIE_LATEST"),
        ([("a1", "HIGH", 50, "post"), ("a1", "LOW", 58, "post"),
          ("a2", "HIGH", 55, "post")], "LOW", 2, "TIE_LATEST"),
        ([("a1", "MEDIUM", 59, "post"), ("a2", "LOW", 50, "post"),
          ("a3", "LOW", 51, "post"), ("a4", "HIGH", 52, "post"),
          ("a5", "HIGH", 53, "post")], "HIGH", 5, "TIE_LATEST"),
        ([], "UNKNOWN", 0, "NO_VALID_STAGE"),
    ],
    ids=["case-1", "case-2", "case-3", "case-4", "case-5", "case-6"],
)
def test_documented_aggregation_cases(
    reports: list[tuple[str, str, int, str]], level: str, report_count: int, decision: str
) -> None:
    """문서의 집계 사례 1~6을 확인한다."""
    end = datetime(2026, 10, 27, 18, 0, tzinfo=timezone(timedelta(hours=9)))
    signals = [
        Judgment(author, end.replace(hour=17, minute=minute), stage,
                 signal_type=kind, signal_id=index)
        for index, (author, stage, minute, kind) in enumerate(reports)
    ]
    trace = aggregator.aggregate_with_trace(signals)
    assert trace.result.model_dump() == {
        "congestion_level": level,
        "report_count": report_count,
        "causes": [{"cause_code": "UNKNOWN", "report_count": 0}] if report_count else [],
    }
    assert trace.decision == decision


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
        "congestion_level": "MEDIUM",
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


def test_trace_keeps_all_source_indices_without_time_filtering() -> None:
    trace_aggregate = getattr(aggregator, "aggregate_with_trace", None)
    assert callable(trace_aggregate)
    trace = trace_aggregate(
        [
            Judgment("expired", CUTOFF, "HIGH"),
            Judgment("a", AS_OF - timedelta(minutes=3), "HIGH", frozenset({"EVENT"})),
            Judgment("a", AS_OF, "LOW"),
        ],
    )

    assert trace.stage_votes[0].signal_indices == (0,)
    assert trace.stage_votes[1].signal_indices == (2,)
    # 최신 유효 제보(인덱스 2)에 원인이 없으므로 원인 선택도 인덱스 2다(U12).
    assert trace.cause_votes[1].signal_indices == (2,)
    assert trace.cause_votes[1].value == frozenset()
    assert trace.stage_counts == {"LOW": 1, "MEDIUM": 0, "HIGH": 1}
    assert trace.result.report_count == 2


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
