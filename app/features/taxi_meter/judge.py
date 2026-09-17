import json

from ...llm import ImageBytes, llm

PROMPT = """
택시미터의 현재 요금만 고르세요.
JSON만 출력하세요. 키: cost
""".strip()


def judge(image: ImageBytes) -> dict:
    raw = llm("openai").complete(image, PROMPT)
    payload = json.loads(raw)
    return {"cost": int(payload["cost"])}