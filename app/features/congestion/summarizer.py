"""최종 코드 집계만 사용해 혼잡도 요약 문장을 만든다."""

from .models import CauseCode, CongestionResult

CAUSE_SENTENCES: dict[CauseCode, str] = {
    "RUSH_HOUR": "출퇴근하는 사람이 많아요.",
    "EVENT": "행사 때문에 사람이 많아요.",
    "ACCIDENT": "사고 때문에 혼잡해요.",
    "SERVICE_ISSUE": "고장이나 운행 중단으로 혼잡해요.",
    "DELAY_ISSUE": "운행이 늦거나 길이 막혀 혼잡해요.",
}
CAUSE_NAMES: dict[CauseCode, str] = {
    "RUSH_HOUR": "출퇴근 인파",
    "EVENT": "행사",
    "ACCIDENT": "사고",
    "SERVICE_ISSUE": "운행 장애",
    "DELAY_ISSUE": "지연·정체",
}
CAUSE_PRIORITY: tuple[CauseCode, ...] = (
    "ACCIDENT",
    "SERVICE_ISSUE",
    "DELAY_ISSUE",
    "EVENT",
    "RUSH_HOUR",
)


def summarize_result(result: CongestionResult) -> str | None:
    """확정된 혼잡 단계와 원인 수로 짧은 상황 문장 하나를 고른다."""
    if result.congestion_level == "UNKNOWN":
        return None
    if result.congestion_level == "LOW" or not result.causes:
        return f"제보한 사람은 {result.report_count}명이에요."
    causes = sorted(
        result.causes,
        key=lambda cause: (-cause.report_count, CAUSE_PRIORITY.index(cause.cause_code)),
    )
    if len(causes) > 1 and causes[0].report_count == causes[1].report_count:
        first, second = (CAUSE_NAMES[cause.cause_code] for cause in causes[:2])
        summary = f"{first}와 {second} 때문에 혼잡해요."
    else:
        summary = CAUSE_SENTENCES[causes[0].cause_code]
    if result.congestion_level == "HIGH":
        prefix, last_word = summary.rsplit(" ", 1)
        summary = f"{prefix} 매우 {last_word}"
    return summary
