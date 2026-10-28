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


@dataclass(frozen=True)
class CallPolicy:
    """시도 한 번의 제한 시간과 429·5xx 재시도 횟수."""

    timeout: float
    retries: int


JUDGE_ENDPOINT = ModelEndpoint(
    "https://api.openai.com/v1/responses", "gpt-6-luna", "OPENAI_API_KEY_2"
)
DECISIONS_ENDPOINT = ModelEndpoint(
    "https://api.openai.com/v1/decisions", "gpt-6-luna", "OPENAI_API_KEY_2"
)
# 임시값이며 실측 뒤 조정한다. 이미지 기능의 제한 시간과 무관하다.
JUDGE_POLICY = CallPolicy(timeout=60.0, retries=1)
DECISIONS_POLICY = CallPolicy(timeout=30.0, retries=1)
