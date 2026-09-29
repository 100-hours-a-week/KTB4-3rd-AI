# judge_rows는 점수표만 보고 pass, reject, review를 정한다.
# choose는 후보마다 "{field}은/는 {후보}이다"를 openjev에 물은 뒤 judge_rows를 부른다.
# 아래 예시의 점수는 샌드박스 테스트에서 openjev가 실제로 낸 값이다. 원문은 oj_candidates.py 상단에 있다.
from dataclasses import dataclass

from ..llm import llm

PASS_ENTAILMENT = 0.75
REJECT_CONTRADICTION = 0.60
MARGIN = 0.15


@dataclass(frozen=True)
class Decision:
    status: str
    value: str | None


def topic(word: str) -> str:
    code = ord(word[-1]) - 0xAC00
    return "은" if 0 <= code < 11172 and code % 28 else "는"
# "표시된 운임"   → "은"
# "이체 완료 정보" → "는"


def judge_rows(candidates: list[str], rows: list[list[float]]) -> Decision:
    if not candidates:
        return Decision("review", None)
    entailment = [row[1] for row in rows]
    winner = max(range(len(entailment)), key=entailment.__getitem__)
    best = entailment[winner]
    second = sorted(entailment)[-2] if len(entailment) > 1 else 0.0
    if best >= PASS_ENTAILMENT and best - second >= MARGIN:
        status = "pass"
    elif rows[winner][0] >= REJECT_CONTRADICTION:
        status = "reject"
    else:
        status = "review"
    return Decision(status, candidates[winner])


def choose(plain: str, field: str, candidates: list[str], device: str) -> Decision:
    if not candidates:
        return Decision("review", None)
    subject = f"{field}{topic(field)}"
    pairs = [(plain, f"{subject} {candidate}이다") for candidate in candidates]
    return judge_rows(candidates, llm("slm", pairs, device=device))
# rows는 가설마다 [모순, 지지, 중립]이다.
#
# 미터기 002, field="표시된 운임", candidates=["3,000원", "1,988원", "114원", "852원"]
#   pairs → [(원문, "표시된 운임은 3,000원이다"), (원문, "표시된 운임은 1,988원이다"), ...]
#   rows  → [[0.01, 0.96, 0.03], [0.07, 0.84, 0.09], [0.32, 0.60, 0.08], ...]
#   1등 0.96, 2등 0.84로 차이가 0.12라 기준 0.15에 못 미친다.
#   → Decision(status="review", value="3,000원")
#
# 영수증 r1, field="이체 완료 정보", candidates=["이체 완료", "보낸분", "홍길동", "받는 분", ...]
#   rows  → [[0.08, 0.84, 0.08], ...]  2등은 "입금 계좌" [0.65, 0.27, 0.08]
#   → Decision(status="pass", value="이체 완료")
#
# 후보가 없으면 openjev를 부르지 않는다. 미터기 002 원문의 datetime_candidates가 []인 경우다.
#   → Decision(status="review", value=None)
