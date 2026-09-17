# API provider - OpenAI API 키
import os
from base64 import b64encode

import httpx

from ..types import ImageBytes

URL = "https://api.openai.com/v1/responses"
MODEL = "gpt-5.6-luna"
TIMEOUT = 60


class OpenAIEngine:
    def complete(self, image: ImageBytes | None, prompt: str) -> str:
        key = os.environ["OPENAI_API_KEY"]
        content: list[dict] = [{"type": "text", "text": prompt}]
        if image is not None:
            b64 = b64encode(image.content).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{image.media_type};base64,{b64}"},
                }
            )
        res = httpx.post(
            URL,
            headers={"Authorization": f"Bearer {key}"},
            json={"model": MODEL, "messages": [{"role": "user", "content": content}]},
            timeout=TIMEOUT,
        )
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]