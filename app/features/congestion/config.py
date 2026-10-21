"""혼잡도 평가에 사용하는 임시 판정값."""

from .relevance import RelevancePolicy

# 실모델 합성 평가에 사용한 값이며 운영 정책으로 승인된 수치는 아니다.
RELEVANCE_POLICY = RelevancePolicy(exclude_below=0.2, related_at_or_above=0.8)
DECISIONS_TIMEOUT_SECONDS = 30.0
