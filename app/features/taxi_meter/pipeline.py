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


def model_api_run(image: ImageBytes) -> dict[str, int]:
    raw = llm("api", PROMPT, image)
    try:
        cost = amount_value(str(json.loads(raw)["cost"]))
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("model output is not valid: %s", type(exc).__name__)
        raise JudgeParseError() from exc
    logger.info("judge parsed: cost=%s", cost)
    return {"cost": cost}
