# 영수증 모델 호출.
# model_pipe_run은 OCR과 openjev로 네 필드를 고르고, model_api_run은 luna API로 영수증을 읽는다.
import json
import re

from ...core.exceptions import JudgeParseError
from ...core.logging import get_logger
from ...core.oj_candidates import (
    amount_candidates,
    amount_value,
    datetime_candidates,
    name_candidates,
    status_candidates,
)
from ...core.oj_decide import choose
from ...llm import ImageBytes, llm

logger = get_logger(__name__)

# 후보가 적어 빨리 끝나는 필드부터 판정한다.
FIELDS = (
    ("amount_cost", "영수증 합계 금액", amount_candidates),
    ("send_time", "보낸 날짜와 시각", datetime_candidates),
    ("transfer_info", "이체 완료 정보", status_candidates),
    ("sender_name", "보낸 사람", name_candidates),
)
KEYS = [key for key, _, _ in FIELDS]
# 이체 완료로 인정하는 문구. 부정·대기 표현이 섞이면 완료가 아니다.
# 예: "이체 완료", "송금이 완료되었어요", "홍길동님께 보냈어요" → 완료 / "이체가 완료되지 않았습니다", "예약 이체" → 아님
# ponytail: 문구 규칙이다. 새 은행 화면이 안 맞으면 여기 단어만 늘린다.
DONE = re.compile(r"완료|보냈")
NOT_DONE = re.compile(r"실패|취소|않|못|대기|예약|(?:진행|처리)\s*중")
PROMPT = """
이체 영수증에서 송금 금액, 이체 결과 문구, 보낸 사람, 보낸 날짜와 시각을 고르세요.
transfer_info에는 화면에 적힌 이체 결과 문구를 그대로 적으세요. 예: "이체 완료"
JSON만 출력하세요. 키: amount_cost, transfer_info, sender_name, send_time
""".strip()


def is_done(transfer_info: str) -> bool:
    return bool(DONE.search(transfer_info)) and not NOT_DONE.search(transfer_info)


def model_pipe_run(image: ImageBytes, device: str) -> dict[str, int | str] | None:
    plain = llm("ocr", image=image, device=device)
    result = {}
    for key, field, candidates in FIELDS:
        decision = choose(plain, field, candidates(plain), device)
        if decision.status != "pass":
            logger.info("judge fallback: %s=%s", key, decision.status)
            return None
        result[key] = decision.value
    if not is_done(result["transfer_info"]):
        logger.info("judge fallback: transfer_info=not_done")
        return None
    result["amount_cost"] = amount_value(result["amount_cost"])
    logger.info("judge parsed: amount_cost=%s", result["amount_cost"])
    return result


def model_api_run(image: ImageBytes) -> dict[str, int | str]:
    raw = llm("api", PROMPT, image)
    try:
        payload = json.loads(raw)
        if any(payload[key] is None for key in KEYS):
            raise ValueError
        result = {key: str(payload[key]) for key in KEYS}
        result["amount_cost"] = amount_value(result["amount_cost"])
        if not is_done(result["transfer_info"]):
            raise ValueError("transfer is not done")
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("model output is not valid: %s", type(exc).__name__)
        raise JudgeParseError() from exc
    logger.info("judge parsed: amount_cost=%s", result["amount_cost"])
    return result
