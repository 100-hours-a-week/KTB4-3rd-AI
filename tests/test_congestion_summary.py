"""집계 결과로 만드는 짧은 요약과 필수 summary 계약을 확인한다."""

from itertools import combinations

import pytest
from pydantic import ValidationError

from app.features.congestion.models import CongestionResult, CongestionResultWithSummary
from app.features.congestion.summarizer import summarize_result


@pytest.mark.parametrize(
    ("level", "causes", "expected"),
    [
        ("UNKNOWN", [], None),
        ("UNKNOWN", [("SERVICE_ISSUE", 1)], None),
        ("LOW", [], "제보한 사람은 2명이에요."),
        ("LOW", [("EVENT", 1)], "제보한 사람은 2명이에요."),
        ("MEDIUM", [], "제보한 사람은 2명이에요."),
        ("HIGH", [], "제보한 사람은 2명이에요."),
        ("HIGH", [("ACCIDENT", 1), ("EVENT", 2)], "행사 때문에 사람이 매우 많아요."),
    ],
)
def test_summary_uses_level_and_most_reported_concrete_cause(
    level: str, causes: list[tuple[str, int]], expected: str | None,
) -> None:
    result = CongestionResult.model_validate({
        "congestion_level": level, "report_count": 2,
        "causes": [{"cause_code": code, "report_count": count} for code, count in causes],
    })
    assert summarize_result(result) == expected


@pytest.mark.parametrize("level", ["MEDIUM", "HIGH"])
@pytest.mark.parametrize(
    ("code", "medium", "high"),
    [
        ("RUSH_HOUR", "출퇴근·등하교하는 사람이 많아요.", "출퇴근·등하교하는 사람이 매우 많아요."),
        ("EVENT", "행사 때문에 사람이 많아요.", "행사 때문에 사람이 매우 많아요."),
        ("ACCIDENT", "사고 때문에 혼잡해요.", "사고 때문에 매우 혼잡해요."),
        ("SERVICE_ISSUE", "고장·점검 등 운행 장애로 혼잡해요.", "고장·점검 등 운행 장애로 매우 혼잡해요."),
        ("DELAY_ISSUE", "운행이 늦거나 길이 막혀 혼잡해요.", "운행이 늦거나 길이 막혀 매우 혼잡해요."),
    ],
)
def test_each_cause_summary_is_fixed_and_short(
    level: str, code: str, medium: str, high: str,
) -> None:
    result = CongestionResult.model_validate({
        "congestion_level": level, "report_count": 1,
        "causes": [{"cause_code": code, "report_count": 1}],
    })
    summary = summarize_result(result)
    assert summary == (high if level == "HIGH" else medium)
    assert len(summary) <= 40


@pytest.mark.parametrize("level", ["MEDIUM", "HIGH"])
@pytest.mark.parametrize(
    ("codes", "names"),
    [
        (["SERVICE_ISSUE", "ACCIDENT"], "사고와 운행 장애"),
        (["DELAY_ISSUE", "SERVICE_ISSUE"], "운행 장애와 지연·정체"),
        (["EVENT", "DELAY_ISSUE"], "지연·정체와 행사"),
        (["RUSH_HOUR", "EVENT"], "행사와 출퇴근·등하교 인파"),
        (["EVENT", "DELAY_ISSUE", "ACCIDENT"], "사고와 지연·정체"),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_equal_cause_counts_join_two_in_priority_order(
    level: str, codes: list[str], names: str, reverse: bool,
) -> None:
    result = CongestionResult.model_validate({
        "congestion_level": level, "report_count": 2,
        "causes": [{"cause_code": code, "report_count": 1}
                   for code in (list(reversed(codes)) if reverse else codes)],
    })
    emphasis = "매우 " if level == "HIGH" else ""
    assert summarize_result(result) == f"{names} 때문에 {emphasis}혼잡해요."


@pytest.mark.parametrize("level", ["MEDIUM", "HIGH"])
@pytest.mark.parametrize("codes", list(combinations(
    ["RUSH_HOUR", "EVENT", "ACCIDENT", "SERVICE_ISSUE", "DELAY_ISSUE"], 2,
)))
def test_all_joined_summaries_fit_length_limit(level: str, codes: tuple[str, str]) -> None:
    result = CongestionResult.model_validate({
        "congestion_level": level, "report_count": 1,
        "causes": [{"cause_code": code, "report_count": 1} for code in codes],
    })
    summary = summarize_result(result)
    assert summary is not None
    assert len(summary) <= 40


@pytest.mark.parametrize("summary", [None, "제보한 사람은 2명이에요."])
def test_summary_key_is_required_but_accepts_null(summary: str | None) -> None:
    data = {"congestion_level": "UNKNOWN", "report_count": 0, "causes": []}
    result = CongestionResultWithSummary.model_validate(dict(data, summary=summary))
    assert result.model_dump(mode="json")["summary"] == summary
    with pytest.raises(ValidationError, match="summary"):
        CongestionResultWithSummary.model_validate(data)


@pytest.mark.parametrize(
    "code",
    ["UNKNOWN", "BREAKDOWN", "DELAY", "SERVICE_DISRUPTION", "WEATHER",
     "ROAD_TRAFFIC", "CONSTRUCTION", "DEMONSTRATION", "ROAD_ISSUE"],
)
def test_result_rejects_removed_cause(code: str) -> None:
    with pytest.raises(ValidationError, match="cause_code"):
        CongestionResult.model_validate({
            "congestion_level": "MEDIUM", "report_count": 1,
            "causes": [{"cause_code": code, "report_count": 0}],
        })
