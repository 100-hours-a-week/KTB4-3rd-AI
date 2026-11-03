"""집계 결과로 만드는 짧은 요약과 필수 summary 계약을 확인한다."""

import pytest
from pydantic import ValidationError

from app.features.congestion.models import CongestionResult, CongestionResultWithSummary
from app.features.congestion.summarizer import summarize_result


@pytest.mark.parametrize(
    ("level", "causes", "expected"),
    [
        ("UNKNOWN", [], None),
        ("UNKNOWN", [("BREAKDOWN", 1)], None),
        ("LOW", [], "제보한 사람은 2명이에요."),
        ("LOW", [("EVENT", 1)], "제보한 사람은 2명이에요."),
        ("MEDIUM", [], "제보한 사람은 2명이에요."),
        ("HIGH", [], "제보한 사람은 2명이에요."),
        ("MEDIUM", [("UNKNOWN", 0)], "제보한 사람은 2명이에요."),
        ("HIGH", [("UNKNOWN", 0)], "제보한 사람은 2명이에요."),
        ("HIGH", [("ACCIDENT", 1), ("EVENT", 2)], "행사 때문에 사람이 많아요."),
        ("MEDIUM", [("UNKNOWN", 5), ("EVENT", 1)], "행사 때문에 사람이 많아요."),
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
    ("code", "expected"),
    [
        ("RUSH_HOUR", "출퇴근하는 사람이 많아요."),
        ("EVENT", "행사 때문에 사람이 많아요."),
        ("ACCIDENT", "사고 때문에 혼잡해요."),
        ("BREAKDOWN", "고장 때문에 운행이 늦어지고 있어요."),
        ("DELAY", "운행이 지연되고 있어요."),
        ("SERVICE_DISRUPTION", "운행이 통제돼서 우회해야 해요."),
        ("WEATHER", "날씨 때문에 혼잡해요."),
        ("ROAD_TRAFFIC", "도로가 막혀서 혼잡해요."),
        ("CONSTRUCTION", "공사 때문에 혼잡해요."),
        ("DEMONSTRATION", "집회 때문에 혼잡해요."),
    ],
)
def test_each_cause_summary_is_fixed_and_short(level: str, code: str, expected: str) -> None:
    result = CongestionResult.model_validate({
        "congestion_level": level, "report_count": 1,
        "causes": [{"cause_code": code, "report_count": 1}],
    })
    summary = summarize_result(result)
    assert summary == expected
    assert len(summary) <= 40


@pytest.mark.parametrize(
    ("preferred", "other", "expected"),
    [
        ("ACCIDENT", "SERVICE_DISRUPTION", "사고 때문에 혼잡해요."),
        ("SERVICE_DISRUPTION", "BREAKDOWN", "운행이 통제돼서 우회해야 해요."),
        ("BREAKDOWN", "DELAY", "고장 때문에 운행이 늦어지고 있어요."),
        ("DELAY", "EVENT", "운행이 지연되고 있어요."),
        ("EVENT", "DEMONSTRATION", "행사 때문에 사람이 많아요."),
        ("DEMONSTRATION", "CONSTRUCTION", "집회 때문에 혼잡해요."),
        ("CONSTRUCTION", "WEATHER", "공사 때문에 혼잡해요."),
        ("WEATHER", "ROAD_TRAFFIC", "날씨 때문에 혼잡해요."),
        ("ROAD_TRAFFIC", "RUSH_HOUR", "도로가 막혀서 혼잡해요."),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_equal_cause_counts_use_priority_regardless_of_input_order(
    preferred: str, other: str, expected: str, reverse: bool,
) -> None:
    codes = [preferred, other] if reverse else [other, preferred]
    result = CongestionResult.model_validate({
        "congestion_level": "HIGH", "report_count": 2,
        "causes": [{"cause_code": code, "report_count": 1} for code in codes],
    })
    assert summarize_result(result) == expected


@pytest.mark.parametrize("summary", [None, "제보한 사람은 2명이에요."])
def test_summary_key_is_required_but_accepts_null(summary: str | None) -> None:
    data = {"congestion_level": "UNKNOWN", "report_count": 0, "causes": []}
    result = CongestionResultWithSummary.model_validate(dict(data, summary=summary))
    assert result.model_dump(mode="json")["summary"] == summary
    with pytest.raises(ValidationError, match="summary"):
        CongestionResultWithSummary.model_validate(data)
