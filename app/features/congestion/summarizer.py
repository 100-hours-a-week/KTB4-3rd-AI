"""최종 코드 집계만 사용해 혼잡도 요약 문장을 만든다."""

from .models import CauseCode, CongestionResult

CAUSE_SENTENCES: dict[CauseCode, str] = {
    "RUSH_HOUR": "출퇴근하는 사람이 많아요.",
    "EVENT": "행사 때문에 사람이 많아요.",
    "ACCIDENT": "사고 때문에 혼잡해요.",
    "BREAKDOWN": "고장 때문에 운행이 늦어지고 있어요.",
    "DELAY": "운행이 지연되고 있어요.",
    "SERVICE_DISRUPTION": "운행이 통제돼서 우회해야 해요.",
    "WEATHER": "날씨 때문에 혼잡해요.",
    "ROAD_TRAFFIC": "도로가 막혀서 혼잡해요.",
    "CONSTRUCTION": "공사 때문에 혼잡해요.",
    "DEMONSTRATION": "집회 때문에 혼잡해요.",
}
CAUSE_PRIORITY: tuple[CauseCode, ...] = (
    "ACCIDENT",
    "SERVICE_DISRUPTION",
    "BREAKDOWN",
    "DELAY",
    "EVENT",
    "DEMONSTRATION",
    "CONSTRUCTION",
    "WEATHER",
    "ROAD_TRAFFIC",
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
