"""최종 요약이 모델 초안 대신 코드 집계의 단계·원인·수를 따르는지 확인한다."""

import pytest

from app.features.congestion.models import CongestionCause, CongestionResult
from app.features.congestion.summarizer import summarize_result


@pytest.mark.parametrize(
    ("level", "label"), [("LOW", "한산"), ("MEDIUM", "보통"), ("HIGH", "매우 혼잡")]
)
def test_known_level_summary_uses_final_stage_and_count(level: str, label: str) -> None:
    result = CongestionResult.model_validate(
        {"congestion_level": level, "report_count": 2, "causes": []}
    )
    assert summarize_result(result) == f"제보 기준 혼잡도는 {label}이며, 혼잡 제보자는 2명입니다."


@pytest.mark.parametrize("report_count", [0, 2])
def test_unknown_result_uses_no_valid_report_summary(report_count: int) -> None:
    result = CongestionResult(congestion_level="UNKNOWN", report_count=report_count, causes=[])
    assert summarize_result(result) == "혼잡 단계를 판단할 유효 제보가 없습니다."


def test_cause_only_summary_preserves_cause_count_without_stage_vote() -> None:
    result = CongestionResult(
        congestion_level="UNKNOWN",
        report_count=0,
        causes=[CongestionCause(cause_code="BREAKDOWN", report_count=1)],
    )
    assert summarize_result(result) == (
        "혼잡 단계를 판단할 유효 제보가 없습니다. 제보된 원인은 차량·설비 고장(1명)입니다."
    )


def test_multiple_causes_use_final_codes_and_counts() -> None:
    result = CongestionResult(
        congestion_level="LOW",
        report_count=1,
        causes=[
            CongestionCause(cause_code="EVENT", report_count=1),
            CongestionCause(cause_code="WEATHER", report_count=2),
        ],
    )
    assert summarize_result(result) == (
        "제보 기준 혼잡도는 한산이며, 혼잡 제보자는 1명입니다. "
        "제보된 원인은 행사·공연·경기(1명), 기상 영향(2명)입니다."
    )
