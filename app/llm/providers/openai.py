# luna 인터페이스. OpenAI Responses API에 이미지와 프롬프트를 보내고 JSON 문자열을 받는다.
import os
from base64 import b64encode

import httpx

from ...core.exceptions import ModelNotReady, UpstreamError
from ...core.logging import get_logger
from ..types import ImageBytes

logger = get_logger(__name__)

URL = "https://api.openai.com/v1/responses"
MODEL = "gpt-6-luna"
TIMEOUT = 60


class OpenAIEngine:
    def complete(self, image: ImageBytes | None, prompt: str) -> str:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            logger.error("openai api key is missing")
            raise ModelNotReady("api model is not ready")

        content: list[dict] = [{"type": "input_text", "text": prompt}]
        if image is not None:
            b64 = b64encode(image.content).decode("ascii")
            content.append({"type": "input_image", "image_url": f"data:{image.media_type};base64,{b64}"})
        body = {
            "model": MODEL,
            "input": [{"role": "user", "content": content}],
            "text": {"format": {"type": "json_object"}},
        }
        try:
            res = httpx.post(URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=TIMEOUT)
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
