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


def test_empty_result_has_nonblank_summary_without_invented_stage() -> None:
    result = CongestionResult(congestion_level="UNKNOWN", report_count=0, causes=[])
    assert summarize_result(result) == "혼잡 단계를 판단할 유효 제보가 없습니다."


def test_tie_summary_keeps_voter_count_and_does_not_pick_stage() -> None:
    result = CongestionResult(
        congestion_level="UNKNOWN",
        report_count=2,
        causes=[CongestionCause(cause_code="UNKNOWN", report_count=0)],
    )
    assert summarize_result(result) == (
        "혼잡 단계가 동률이라 현재 단계를 확정하기 어렵습니다. 혼잡 제보자는 2명입니다. "
        "구체적인 혼잡 원인은 제보되지 않았습니다."
    )


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
