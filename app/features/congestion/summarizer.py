"""최종 코드 집계만 사용해 혼잡도 요약 문장을 만든다."""

from .models import CauseCode, CongestionResult

CAUSE_SENTENCES: dict[CauseCode, str] = {
    "RUSH_HOUR": "출퇴근하는 사람이 많아요.",
    "EVENT": "행사 때문에 사람이 많아요.",
    "ACCIDENT": "사고 때문에 혼잡해요.",
    "SERVICE_ISSUE": "운행에 문제가 생겨 혼잡해요.",
    "ROAD_ISSUE": "도로에 문제가 생겨 혼잡해요.",
}
CAUSE_PRIORITY: tuple[CauseCode, ...] = (
    "ACCIDENT",
    "SERVICE_ISSUE",
    "ROAD_ISSUE",
    "EVENT",
    "RUSH_HOUR",
)


def summarize_result(result: CongestionResult) -> str | None:
    """확정된 혼잡 단계와 원인 수로 짧은 상황 문장 하나를 고른다."""
    if result.congestion_level == "UNKNOWN":
        return None
    if result.congestion_level == "LOW" or not result.causes:
        return f"제보한 사람은 {result.report_count}명이에요."
    cause = min(
        result.causes,
        key=lambda cause: (-cause.report_count, CAUSE_PRIORITY.index(cause.cause_code)),
    )
    return CAUSE_SENTENCES[cause.cause_code]
