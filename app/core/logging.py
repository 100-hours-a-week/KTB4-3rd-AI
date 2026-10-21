import logging
import re
from contextvars import ContextVar
from uuid import uuid4

import httpx

# 로그 설정은 이 파일에서만 한다. get_logger()를 처음 부를 때 한 번 적용된다.
# uvicorn이 핸들러를 이미 붙였다면 포맷은 그대로 두고 app 로거 레벨만 INFO로 맞춘다.
# 요청 안에서 남긴 app 로그 끝에는 request_id=<id>가 붙는다. 같은 요청의 로그를 이 값으로 묶는다.
# 실패 로그는 "무엇이 실패: 어디서, 왜(reason), 무엇을 확인(hint)" 순서로 쓴다.
#
# 로그를 남기는 시점
# - features/receipt/image.py, features/taxi_meter/image.py
#   - INFO    image loaded: 다운로드와 형식 검사를 통과했을 때. host, bytes, type, ms
#   - WARNING image download failed: host, reason(timeout|connect|redirect|http_4xx|http_5xx|bad_url), status, ms
#   - WARNING image size is invalid: 비었거나 5MB를 넘었을 때. host, bytes
#   - WARNING image must be jpeg or png: host, reason(not_image|wrong_type), type, bytes
# - features/taxi_meter/main.py, features/receipt/main.py
#   - INFO    request route: 어느 경로로 응답했는지. route(pipe|luna), reason(device_off|model_not_ready|judge_fallback ...)
#   - WARNING image model failed: OCR, openjev, Modal이 실행 중 실패해 luna로 넘길 때. code, device
# - features/taxi_meter/pipeline.py, features/receipt/pipeline.py
#   - INFO    judge parsed: pass 결과로 응답을 만들었을 때
#   - INFO    judge fallback: pass가 아니라 luna로 넘길 때. 멈춘 필드와 status
#   - WARNING model output is not valid: luna 응답으로 값을 확정하지 못했을 때. field, reason(not_json|unreadable|not_amount|transfer_not_done)
# - main.py
#   - WARNING request failed: 4xx 오류 응답을 줄 때. path, status, code, hint
#   - ERROR   request failed: 5xx 오류 응답을 줄 때. path, status, code, hint
#   - ERROR   unhandled error: AppError가 아닌 예외. path, error. 스택은 로그에만 남긴다
# - llm/router.py
#   - ERROR   unknown model: 등록되지 않은 모델 이름
#   - WARNING model marked down: 준비에 실패해 재시작 전까지 luna로만 처리할 때. model, device
# - llm/providers/openai.py
#   - ERROR   openai api key is missing: OPENAI_API_KEY_1 또는 _2가 없을 때. 빠진 키 이름. _1은 예전 이름 OPENAI_API_KEY도 읽는다
#   - WARNING openai request retry: 429 또는 5xx를 받아 한 번 더 보낼 때. status, error
#   - ERROR   openai request failed: reason, status, error(type/code), openai_req(x-request-id), ms, hint
#   - ERROR   openai response shape is unexpected: 응답에 output_text가 없을 때. reason, ms
# - llm/providers/ocr.py
#   - WARNING image model unavailable: paddleocr가 설치되지 않음(v2 기본 설치). model, missing
#   - ERROR   image model import failed: 설치는 됐는데 import가 깨짐. 스택을 남긴다. model, missing
#   - ERROR   ocr model load failed: PP-OCRv6_medium_det 로드에 실패했을 때. device
#   - ERROR   ocr inference failed: 추론에 실패했을 때. bytes, type. 스택은 로그에만 남긴다
# - llm/providers/modal_gpu.py
#   - WARNING image model unavailable: modal이 설치되지 않음. model, missing
#   - ERROR   modal lookup failed: 토큰이 틀렸거나 그 계정에 앱이 없을 때. reason, slot, hint
#   - ERROR   modal call failed: method, reason(timeout|auth|not_found|remote_error), error, ms
# - llm/providers/slm.py
#   - WARNING image model unavailable: torch 또는 transformers가 설치되지 않음. model, missing
#   - ERROR   slm model load failed: AlexWortega/openjev 4B 로드에 실패했을 때. device
#   - ERROR   slm inference failed: 추론 중 예외가 났을 때. pairs, max_chars. 스택은 로그에만 남긴다
#
# 이미지 바이트, base64, API 키, 모델 원문, 사람 이름은 로그에 넣지 않는다. URL은 호스트만 남긴다.

READY = False
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContext(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        rid = request_id.get()
        if rid and not getattr(record, "request_id", None):
            record.request_id = rid
            record.msg = f"{record.msg} request_id={rid}"
        return True


CONTEXT = RequestContext()


def setup_logging() -> None:
    global READY
    logging.getLogger("app").setLevel(logging.INFO)
    if READY or logging.getLogger().handlers:
        READY = True
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    READY = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if CONTEXT not in logger.filters:
        logger.addFilter(CONTEXT)
    return logger


async def request_context(request, call_next):
    # Spring이 X-Request-ID를 보내면 그 값을 쓰고, 없으면 새로 만든다. 응답 헤더로 돌려줘 양쪽 로그를 맞춘다.
    given = request.headers.get("x-request-id", "")
    rid = given if SAFE_ID.match(given) else uuid4().hex[:12]
    token = request_id.set(rid)
    try:
        response = await call_next(request)
    finally:
        request_id.reset(token)
    response.headers["X-Request-ID"] = rid
    return response


def http_reason(exc: Exception) -> str:
    # httpx 예외를 바로 읽히는 원인 이름으로 바꾼다.
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return "redirect" if exc.response.is_redirect else f"http_{exc.response.status_code // 100}xx"
    if isinstance(exc, httpx.ConnectError):
        return "connect"
    if isinstance(exc, (httpx.UnsupportedProtocol, httpx.InvalidURL)):
        return "bad_url"
    return type(exc).__name__
# httpx.ConnectTimeout       → "timeout"
# 301 응답의 HTTPStatusError  → "redirect"
# 404 응답의 HTTPStatusError  → "http_4xx"
# "ftp://x"                  → "bad_url"


def log_import_error(logger: logging.Logger, model: str, packages: tuple[str, ...], exc: ImportError) -> None:
    # 패키지 자체가 없으면 이 환경에서 그 장치를 쓰지 않는 것으로 보고 WARNING 한 줄만 남긴다(v2 기본 설치).
    # 설치는 됐는데 하위 의존성이 없거나 import가 깨졌으면 고쳐야 하는 문제라 ERROR와 스택을 남긴다.
    missing = (getattr(exc, "name", None) or "").split(".")[0]
    if isinstance(exc, ModuleNotFoundError) and missing in packages:
        logger.warning("image model unavailable: model=%s missing=%s", model, missing)
    else:
        logger.exception("image model import failed: model=%s missing=%s", model, missing or type(exc).__name__)
