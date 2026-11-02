"""혼잡도 평가에 사용하는 임시 판정값."""

from dataclasses import dataclass

from .relevance import RelevancePolicy

# 실모델 합성 평가에 사용한 값이며 운영 정책으로 승인된 수치는 아니다.
RELEVANCE_POLICY = RelevancePolicy(exclude_below=0.2, related_at_or_above=0.8)


@dataclass(frozen=True)
class ModelEndpoint:
    """호출할 URL·모델 이름·키의 환경변수 이름."""

    url: str
    model: str
    key_env: str
    provider: str = "openai"


@dataclass(frozen=True)
class CallPolicy:
    """시도 한 번의 제한 시간, 429·5xx 재시도 횟수와 동시 호출 수."""

    timeout: float
    retries: int
    concurrency: int


JUDGE_ENDPOINTS = (
    ModelEndpoint("https://api.openai.com/v1/responses", "gpt-6-luna", "OPENAI_API_KEY_2"),
    ModelEndpoint(
        "https://api.anthropic.com/v1/messages", "claude-haiku-5-5",
        "ANTHROPIC_API_KEY", "anthropic",
    ),
)
JUDGE_MAX_TOKENS = 8192
DECISIONS_ENDPOINT = ModelEndpoint(
    "https://api.openai.com/v1/decisions", "gpt-6-luna", "OPENAI_API_KEY_2"
)
# 임시값이며 실측 뒤 조정한다. 이미지 기능의 제한 시간과 무관하다.
JUDGE_POLICY = CallPolicy(timeout=60.0, retries=1, concurrency=5)
DECISIONS_POLICY = CallPolicy(timeout=30.0, retries=1, concurrency=10)

DECISIONS_MAX_QUESTIONS = 400

RELEVANCE_KEYWORDS = (
    "혼잡", "붐비", "붐빔", "사람많", "사람이많", "꽉", "만원", "미어터", "북적", "복잡",
    "한산", "여유", "텅", "널널", "사람없", "사람이없",
    "줄", "대기", "기다", "못타", "못탔", "탑승", "배차", "밀려", "밀림", "막히", "막혀", "정체",
    "지연", "연착", "사고", "고장", "공사", "시위", "집회", "행사", "공연", "경기", "폭우", "폭설", "출근", "퇴근",
)
