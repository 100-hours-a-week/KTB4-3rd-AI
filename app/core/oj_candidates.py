# OCR 원문에서 필드별 후보를 만든다.
# 같은 후보가 두 번 있으면 1, 2등 점수 차이가 0이 되어 pass가 나오지 않으므로 중복을 뺀다.
#
# 아래 예시는 샌드박스 테스트에서 실제로 나온 OCR 원문이다. 줄바꿈은 \n으로 적는다.
# - 미터기 002: "뉴·프로+\n34 0.0 Km/h\n3000\n원\ntNON\n0흙\n0%\n1988\n주영\n1.주거리 k 2영리 kn 3.법수 40수 5.신요\n할증복합\n$.N0:114.852\n주행\n빈차"
# - 영수증 r1: "이체 완료\n15,400원\n보낸분\n홍길동\n받는 분\n김철수\n입금 계좌\n카카오뱅크3333-01-1234567\n보낸 날짜\n2026-09-2312:30:45"
# - 영수증 r3: "이체완료\n32,000원\n입금자명\n최지우\n수수료\n0원\n거래일시\n2026년9월 1일오후 7:42"
import re

DATE = r"\d{2,4}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2}\.?|\d{2,4}년\s*\d{1,2}월\s*\d{1,2}일"
TIME = r"(?:(?:오전|오후)\s*)?\d{1,2}:\d{2}(?::\d{2})?(?!\d)"
# 날짜 바로 뒤에 숫자가 이어지면 계좌번호의 일부로 보고 날짜로 잡지 않는다.
DATETIME = re.compile(rf"(?:{DATE})(?:\s*(?:{TIME})|(?!\d))|(?<!\d)(?:{TIME})")
NOT_AMOUNT = re.compile(rf"{DATETIME.pattern}|\d+(?:-\d+)+")
AMOUNT = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")
MIN_AMOUNT = 100
WORD = re.compile(r"[가-힣][가-힣*]*")
HONORIFIC = re.compile(r"(님께|님|께)$")
LABELS = {"보낸분", "보내는분", "보내는", "보낸", "받는분", "받는", "입금자", "입금자명", "예금주", "송금인", "수취인"}


def unique(items) -> list[str]:
    return list(dict.fromkeys(items))
# ["15,400원", "15,400원", "3,000원"] → ["15,400원", "3,000원"]


def amount_value(span: str) -> int:
    return int(span.replace(",", "").replace(" ", "").replace("원", ""))
# "15,400원" → 15400
# "3000"     → 3000


def amount_candidates(plain: str) -> list[str]:
    # 날짜, 시각, 전화·계좌번호를 먼저 지운다. 그대로 두면 숫자 조각이 금액 후보로 들어간다.
    text = NOT_AMOUNT.sub(" ", plain)
    values = (amount_value(span) for span in AMOUNT.findall(text))
    return unique(f"{value:,}원" for value in values if value >= MIN_AMOUNT)
# 미터기 002 → ["3,000원", "1,988원", "114원", "852원"]
# 영수증 r1  → ["15,400원"]
# 영수증 r3  → ["32,000원"]


def datetime_candidates(plain: str) -> list[str]:
    return unique(" ".join(span.split()) for span in DATETIME.findall(plain))
# 미터기 002 → []
# 영수증 r1  → ["2026-09-2312:30:45"]
# 영수증 r3  → ["2026년9월 1일오후 7:42"]


def name_candidates(plain: str) -> list[str]:
    names = (HONORIFIC.sub("", word) for word in WORD.findall(plain))
    return unique(name for name in names if 2 <= len(name) <= 4 and name not in LABELS)
# 영수증 r1 → ["이체", "완료", "홍길동", "김철수", "입금", "계좌", "날짜"]
# 영수증 r3 → ["이체완료", "최지우", "수수료", "거래일시", "일오후"]


def status_candidates(plain: str) -> list[str]:
    # 이체 상태 문구에는 숫자가 없다. 금액이나 날짜 줄이 섞이면 정답과 점수가 비슷하게 나온다.
    lines = (line.strip() for line in plain.splitlines())
    return unique(line for line in lines if line and not any(ch.isdigit() for ch in line))
# 영수증 r1 → ["이체 완료", "보낸분", "홍길동", "받는 분", "김철수", "입금 계좌", "보낸 날짜"]
# 영수증 r3 → ["이체완료", "입금자명", "최지우", "수수료", "거래일시"]
