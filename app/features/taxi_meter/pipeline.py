# 미터기 모델 호출.
# model_pipe_run은 OCR과 openjev로 요금을 고르고, model_api_run은 luna API로 요금을 읽는다.
import json

from ...core.exceptions import JudgeParseError
from ...core.logging import get_logger
from ...core.oj_candidates import amount_candidates, amount_value
from ...core.oj_decide import judge_rows, topic
from ...llm import ImageBytes, llm

logger = get_logger(__name__)

FIELD = "표시된 운임"
PROMPT = """
택시미터의 현재 요금만 고르세요.
JSON만 출력하세요. 키: cost
""".strip()
# 읽지 못하면 null을 준다. null은 JUDGE_PARSE_FAILED가 된다.
SCHEMA = {
    "type": "object",
    "properties": {"cost": {"type": ["integer", "null"]}},
    "required": ["cost"],
    "additionalProperties": False,
}


def model_pipe_run(image: ImageBytes, device: str) -> dict[str, int] | None:
    plain = llm("ocr", image=image, device=device)
    candidates = amount_candidates(plain)
    if not candidates:
        logger.info("judge fallback: status=review")
        return None
    subject = f"{FIELD}{topic(FIELD)}"
    pairs = [(plain, f"{subject} {candidate}이다") for candidate in candidates]
    decision = judge_rows(candidates, llm("slm", pairs, device=device))
    if decision.status != "pass":
        logger.info("judge fallback: status=%s", decision.status)
        return None
    cost = amount_value(decision.value)
    logger.info("judge parsed: cost=%s", cost)
    return {"cost": cost}


def invalid(field: str, reason: str) -> JudgeParseError:
    logger.warning("model output is not valid: field=%s reason=%s", field, reason)
    return JudgeParseError()


def model_api_run(image: ImageBytes) -> dict[str, int]:
    raw = llm("api_1", PROMPT, image, schema=SCHEMA)
    try:
        value = json.loads(raw).get("cost")
    except (ValueError, AttributeError) as exc:
        raise invalid("cost", "not_json") from exc
    if value is None:
        # luna가 사진에서 요금을 읽지 못해 null을 줬다.
        raise invalid("cost", "unreadable")
    try:
        cost = amount_value(str(value))
    except ValueError as exc:
        raise invalid("cost", "not_amount") from exc
    logger.info("judge parsed: cost=%s", cost)
    return {"cost": cost}
