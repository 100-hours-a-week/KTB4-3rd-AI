# luna 인터페이스. OpenAI Responses API에 이미지와 프롬프트를 보내고 JSON 문자열을 받는다.
# schema를 주면 Structured Outputs(strict)로 키와 타입을 API가 강제한다. 없으면 JSON 문법만 강제한다.
import asyncio
import os
import time
from base64 import b64encode
from io import BytesIO

import httpx
from PIL import Image

from ...Server_Monitor_Bot.hooks import drill_point, flag, last_call
from ...core.exceptions import ModelNotReady, UpstreamError
from ...core.logging import get_logger, http_reason
from ..types import ImageBytes

logger = get_logger(__name__)

URL = "https://api.openai.com/v1/responses"
MODEL = "gpt-6-luna"
# 재시도까지 합친 OpenAI 구간 상한. 실측(2026-10-15, 75회) 최대 30.4초의 1.5배다.
# BE read-timeout 60초 = 이미지 다운로드 최악 10초 + 45초 + 여유 5초.
TIMEOUT = 45
RETRY_WAIT = 1.0
# 재시도는 남은 시간이 이만큼 있을 때만 한다.
RETRY_MIN = 10
# 상태 코드별 확인 안내. 응답 본문의 error.code가 더 구체적이면 그것도 함께 남긴다.
HINTS = {
    400: "요청 본문 확인(schema, 이미지 형식)",
    401: "API 키가 틀렸거나 폐기됨. OPENAI_API_KEY_1 값 확인",
    403: "키의 조직·프로젝트 권한 또는 지역 제한 확인",
    404: f"모델 이름 확인(코드 고정 {MODEL})",
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


def resize(image: ImageBytes | None) -> ImageBytes | None:
    side = int(flag("image.max_side_px", 0) or 0)
    if image is None or side <= 0 or max(image.width, image.height) <= side:
        return image
    with Image.open(BytesIO(image.content)) as img:
        img.thumbnail((side, side))
        out = BytesIO()
        fmt = "PNG" if image.media_type == "image/png" else "JPEG"
        img.save(out, format=fmt)
        width, height = img.size
    return ImageBytes(out.getvalue(), width, height, image.media_type, image.source)


_limit: tuple[int, asyncio.Semaphore] | None = None


def semaphore() -> asyncio.Semaphore | None:
    global _limit
    n = int(flag("llm.max_concurrency", 0) or 0)
    if n <= 0:
        return None
    if _limit is None or _limit[0] != n:
        _limit = (n, asyncio.Semaphore(n))
    return _limit[1]


class OpenAIEngine:
    def __init__(self, slot: str):
        self.key_name = f"OPENAI_API_KEY_{slot}"
        self.model = MODEL

    async def complete(self, image: ImageBytes | None, prompt: str, schema: dict | None = None) -> str:
        key = os.environ.get(self.key_name)
        if not key and self.key_name == "OPENAI_API_KEY_1":
            # 키를 _1, _2로 나누기 전 이름. 배포 Secret을 아직 안 바꿨어도 이미지 기능은 돌게 한다.
            key = os.environ.get("OPENAI_API_KEY")
        if not key:
            logger.error("openai api key is missing: %s hint=컨테이너 환경변수에 %s 또는 OPENAI_API_KEY 주입 확인", self.key_name, self.key_name)
            raise ModelNotReady()

        info = {"model": self.model, "attempts": 0}
        last_call.set(info)
        image = resize(image)
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
            res = await self.limited(key, body, info, started + TIMEOUT)
            info.update(status=res.status_code, openai_req=res.headers.get("x-request-id"))
            res.raise_for_status()
            payload = res.json()
            usage = payload.get("usage") or {}
            info.update(input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))
            output = payload["output"]
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
            code = error_detail(res).split("/")[-1]
            info.update(status=status, reason=http_reason(exc), error_code=None if code in ("", "-") else code)
            hint = HINTS.get(status) or ("OpenAI 서버 오류. 잠시 뒤 재시도" if (status or 0) >= 500 else f"{TIMEOUT}초 안에 응답 없음 또는 네트워크 확인")
            logger.error(
                "openai request failed: reason=%s status=%s error=%s openai_req=%s model=%s ms=%s hint=%s",
                http_reason(exc), status, error_detail(res), res.headers.get("x-request-id") if res is not None else None,
                self.model, int((time.monotonic() - started) * 1000), hint,
            )
            raise UpstreamError() from exc
        except (KeyError, TypeError, ValueError) as exc:
            info.update(reason="shape")
            logger.error("openai response shape is unexpected: reason=%s ms=%s", type(exc).__name__, int((time.monotonic() - started) * 1000))
            raise UpstreamError() from exc
        if not text:
            info.update(reason="shape")
            logger.error("openai response shape is unexpected: reason=empty_output_text ms=%s", int((time.monotonic() - started) * 1000))
            raise UpstreamError()
        return text

    async def limited(self, key: str, body: dict, info: dict, deadline: float) -> httpx.Response:
        fake = drill_point("luna_call")
        if fake:
            await asyncio.sleep(float(fake.get("delay_s", 0)))
            status = int(fake.get("status", 429))
            err = {"error": {"type": fake.get("error_type", "requests"), "code": fake.get("error_code", "rate_limit_exceeded")}}
            return httpx.Response(status, json=err, request=httpx.Request("POST", URL))
        sem = semaphore()
        if sem is None:
            return await self.post(key, body, info, deadline)
        try:
            await asyncio.wait_for(sem.acquire(), max(0.0, deadline - time.monotonic() - RETRY_MIN))
        except asyncio.TimeoutError:
            info.update(reason="concurrency_wait")
            ms = int((time.monotonic() - deadline + TIMEOUT) * 1000)
            logger.error("openai request failed: reason=concurrency_wait status=None error=-/- openai_req=None model=%s ms=%s "
                         "hint=동시 호출 상한(llm.max_concurrency) 대기 초과. 모니터 조정 원복 확인", self.model, ms)
            raise UpstreamError()
        try:
            return await self.post(key, body, info, deadline)
        finally:
            sem.release()

    async def post(self, key: str, body: dict, info: dict | None = None, deadline: float | None = None) -> httpx.Response:
        # ponytail: 429와 5xx만 1초 뒤 한 번 더 보내고, 두 번 합쳐 TIMEOUT 안에서 끝낸다. 429가 잦으면 Retry-After를 읽는다.
        deadline = deadline or time.monotonic() + TIMEOUT
        async with httpx.AsyncClient() as client:
            for attempt in (1, 2):
                if info is not None:
                    info["attempts"] = attempt
                res = await client.post(URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=deadline - time.monotonic())
                retryable = res.status_code == 429 or res.status_code >= 500
                if attempt == 2 or not retryable or deadline - time.monotonic() - RETRY_WAIT < RETRY_MIN:
                    return res
                logger.warning("openai request retry: status=%s error=%s", res.status_code, error_detail(res))
                await asyncio.sleep(RETRY_WAIT)
