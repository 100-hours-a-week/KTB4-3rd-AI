# luna 인터페이스. OpenAI Responses API에 이미지와 프롬프트를 보내고 JSON 문자열을 받는다.
# schema를 주면 Structured Outputs(strict)로 키와 타입을 API가 강제한다. 없으면 JSON 문법만 강제한다.
import os
import time
from base64 import b64encode

import httpx

from ...core.exceptions import ModelNotReady, UpstreamError
from ...core.logging import get_logger, http_reason
from ..types import ImageBytes

logger = get_logger(__name__)

URL = "https://api.openai.com/v1/responses"
MODEL = "gpt-6-luna"
TIMEOUT = 60
RETRY_WAIT = 1.0
# 상태 코드별 확인 안내. 응답 본문의 error.code가 더 구체적이면 그것도 함께 남긴다.
HINTS = {
    400: "요청 본문 확인(schema, 이미지 형식)",
    401: "API 키가 틀렸거나 폐기됨. OPENAI_API_KEY_1 값 확인",
    403: "키의 조직·프로젝트 권한 또는 지역 제한 확인",
    404: "모델 이름 확인(OPENAI_MODEL_1, 기본 gpt-6-luna)",
    429: "요청 한도 초과 또는 크레딧 부족(error 값이 insufficient_quota면 결제 확인)",
}


def error_detail(res: httpx.Response | None) -> str:
    # OpenAI 오류 본문의 error.type/error.code만 꺼낸다. 메시지 원문은 남기지 않는다.
    try:
        err = res.json()["error"]
        return f"{err.get('type') or '-'}/{err.get('code') or '-'}"
    except Exception:
        return "-"
# {"error": {"type": "insufficient_quota", "code": "insufficient_quota"}} → "insufficient_quota/insufficient_quota"


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
            logger.error("openai api key is missing: %s hint=컨테이너 환경변수에 %s 또는 OPENAI_API_KEY 주입 확인", self.key_name, self.key_name)
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
        started = time.monotonic()
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
            res = getattr(exc, "response", None)
            status = getattr(res, "status_code", None)
            hint = HINTS.get(status) or ("OpenAI 서버 오류. 잠시 뒤 재시도" if (status or 0) >= 500 else f"{TIMEOUT}초 안에 응답 없음 또는 네트워크 확인")
            logger.error(
                "openai request failed: reason=%s status=%s error=%s openai_req=%s model=%s ms=%s hint=%s",
                http_reason(exc), status, error_detail(res), res.headers.get("x-request-id") if res is not None else None,
                self.model, int((time.monotonic() - started) * 1000), hint,
            )
            raise UpstreamError() from exc
        except (KeyError, TypeError, ValueError) as exc:
            logger.error("openai response shape is unexpected: reason=%s ms=%s", type(exc).__name__, int((time.monotonic() - started) * 1000))
            raise UpstreamError() from exc
        if not text:
            logger.error("openai response shape is unexpected: reason=empty_output_text ms=%s", int((time.monotonic() - started) * 1000))
            raise UpstreamError()
        return text

    def post(self, key: str, body: dict) -> httpx.Response:
        # ponytail: 429와 5xx만 1초 뒤 한 번 더 보낸다. 최악 지연은 TIMEOUT의 두 배다. 429가 잦으면 Retry-After를 읽는다.
        for attempt in (1, 2):
            res = httpx.post(URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=TIMEOUT)
            if attempt == 2 or (res.status_code != 429 and res.status_code < 500):
                return res
            logger.warning("openai request retry: status=%s error=%s", res.status_code, error_detail(res))
            time.sleep(RETRY_WAIT)
