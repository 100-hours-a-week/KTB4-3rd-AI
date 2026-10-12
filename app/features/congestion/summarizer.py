"""최종 코드 집계만 사용해 혼잡도 요약 문장을 만든다."""

from .models import CauseCode, CongestionLevel, CongestionResult

LEVEL_LABELS: dict[CongestionLevel, str] = {
    "LOW": "한산",
    "MEDIUM": "보통",
    "HIGH": "매우 혼잡",
    "UNKNOWN": "판단 불가",
}
CAUSE_LABELS: dict[CauseCode, str] = {
    "RUSH_HOUR": "출퇴근 시간대",
    "EVENT": "행사·공연·경기",
    "ACCIDENT": "사고",
    "BREAKDOWN": "차량·설비 고장",
    "DELAY": "운행 지연",
    "SERVICE_DISRUPTION": "운행 중단·통제·우회",
    "WEATHER": "기상 영향",
    "ROAD_TRAFFIC": "도로 정체",
    "CONSTRUCTION": "공사·시설 작업",
    "DEMONSTRATION": "집회·시위·행진",
    "UNKNOWN": "원인 미상",
}


def summarize_result(result: CongestionResult) -> str:
    """원문·모델 초안 없이 확정된 집계 값만 문장으로 표현한다."""
    if result.congestion_level != "UNKNOWN":
        stage = (
            f"제보 기준 혼잡도는 {LEVEL_LABELS[result.congestion_level]}이며, "
            f"혼잡 제보자는 {result.report_count}명입니다."
        )
    elif result.report_count:
        stage = (
            "혼잡 단계가 동률이라 현재 단계를 확정하기 어렵습니다. "
            f"혼잡 제보자는 {result.report_count}명입니다."
        )
    else:
        stage = "혼잡 단계를 판단할 유효 제보가 없습니다."
    concrete = [cause for cause in result.causes if cause.cause_code != "UNKNOWN"]
    if concrete:
        causes = ", ".join(
            f"{CAUSE_LABELS[cause.cause_code]}({cause.report_count}명)" for cause in concrete
        )
        return f"{stage} 제보된 원인은 {causes}입니다."
    if result.causes:
        return f"{stage} 구체적인 혼잡 원인은 제보되지 않았습니다."
    return stage
