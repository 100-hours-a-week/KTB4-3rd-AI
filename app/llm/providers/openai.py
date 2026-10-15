# luna 인터페이스. OpenAI Responses API에 이미지와 프롬프트를 보내고 JSON 문자열을 받는다.
# schema를 주면 Structured Outputs(strict)로 키와 타입을 API가 강제한다. 없으면 JSON 문법만 강제한다.
import os
import time
from base64 import b64encode

import httpx

from ...core.exceptions import ModelNotReady, UpstreamError
from ...core.logging import get_logger
from ..types import ImageBytes

logger = get_logger(__name__)

URL = "https://api.openai.com/v1/responses"
MODEL = "gpt-6-luna"
TIMEOUT = 60
RETRY_WAIT = 1.0


class OpenAIEngine:
    def __init__(self, slot: str):
        self.key_name = f"OPENAI_API_KEY_{slot}"
        self.model = os.environ.get(f"OPENAI_MODEL_{slot}") or MODEL

    def complete(self, image: ImageBytes | None, prompt: str, schema: dict | None = None) -> str:
        key = os.environ.get(self.key_name)
        if not key and self.key_name == "OPENAI_API_KEY_1":
            # 키를 _1, _2로 나누기 전 이름. 배포 Secret을 아직 안 바꿨어도 이미지 기능은 돌게 한다.
            key = os.environ.get("OPENAI_API_KEY")
        if not key:
            logger.error("openai api key is missing: %s", self.key_name)
            raise ModelNotReady()

        content: list[dict] = [{"type": "input_text", "text": prompt}]
        if image is not None:
            b64 = b64encode(image.content).decode("ascii")
            content.append({"type": "input_image", "image_url": f"data:{image.media_type};base64,{b64}"})
        body = {
            "model": self.model,
            "input": [{"role": "user", "content": content}],
            "text": {"format": {"type": "json_schema", "name": "result", "schema": schema, "strict": True}
                     if schema else {"type": "json_object"}},
        }
        try:
            res = self.post(key, body)
            res.raise_for_status()
            output = res.json()["output"]
            text = "".join(
                part["text"]
                for item in output
                if item["type"] == "message"
                for part in item["content"]
                if part["type"] == "output_text"
            )
        except httpx.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            logger.error("openai request failed: status=%s", status)
            raise UpstreamError() from exc
        except (KeyError, TypeError, ValueError) as exc:
            logger.error("openai response shape is unexpected: %s", type(exc).__name__)
            raise UpstreamError() from exc
        if not text:
            logger.error("openai response shape is unexpected: empty output_text")
            raise UpstreamError()
        return text

    def post(self, key: str, body: dict) -> httpx.Response:
        # ponytail: 429와 5xx만 1초 뒤 한 번 더 보낸다. 최악 지연은 TIMEOUT의 두 배다. 429가 잦으면 Retry-After를 읽는다.
        for attempt in (1, 2):
            res = httpx.post(URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=TIMEOUT)
            if attempt == 2 or (res.status_code != 429 and res.status_code < 500):
                return res
            logger.warning("openai request retry: status=%s", res.status_code)
            time.sleep(RETRY_WAIT)
