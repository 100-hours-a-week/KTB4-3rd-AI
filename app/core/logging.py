import logging

# 로그 설정은 이 파일에서만 한다. get_logger()를 처음 부를 때 한 번 적용된다.
# uvicorn이 핸들러를 이미 붙였다면 포맷은 그대로 두고 app 로거 레벨만 INFO로 맞춘다.
#
# 로그를 남기는 시점
# - features/receipt/image.py, features/taxi_meter/image.py
#   - INFO    image loaded: 다운로드와 형식 검사를 통과했을 때. host, bytes, type
#   - WARNING image download failed: 타임아웃, 연결 실패, 4xx, 5xx일 때. host, status
#   - WARNING image size is invalid: 비었거나 5MB를 넘었을 때. host, bytes
#   - WARNING image must be jpeg or png: 디코드에 실패했거나 jpeg, png가 아닐 때. host
# - features/taxi_meter/main.py, features/receipt/main.py
#   - WARNING image model failed: OCR이나 openjev가 실패해 luna로 넘길 때. code
# - features/taxi_meter/pipeline.py, features/receipt/pipeline.py
#   - INFO    judge parsed: pass 결과로 응답을 만들었을 때
#   - INFO    judge fallback: pass가 아니라 luna로 넘길 때. 멈춘 필드와 status
#   - WARNING model output is not valid: luna가 준 JSON에 필요한 값이 없을 때
# - main.py
#   - ERROR   unhandled error: AppError가 아닌 예외. 스택은 로그에만 남긴다
# - llm/router.py
#   - ERROR   unknown model: 등록되지 않은 모델 이름
#   - WARNING model marked down: 준비에 실패해 재시작 전까지 luna로만 처리할 때. model, device
# - llm/providers/openai.py
#   - ERROR   openai api key is missing: OPENAI_API_KEY_1 또는 _2가 없을 때. 빠진 키 이름. _1은 예전 이름 OPENAI_API_KEY도 읽는다
#   - WARNING openai request retry: 429 또는 5xx를 받아 한 번 더 보낼 때. status
#   - ERROR   openai request failed: OpenAI HTTP 요청이 실패했을 때. status
#   - ERROR   openai response shape is unexpected: 응답 output에 output_text가 없을 때
# - llm/providers/ocr.py
#   - WARNING image model unavailable: paddleocr가 설치되지 않음(v2 기본 설치). model, missing
#   - ERROR   image model import failed: 설치는 됐는데 import가 깨짐. 스택을 남긴다. model, missing
#   - ERROR   ocr model load failed: PP-OCRv6_medium_det 로드에 실패했을 때
#   - ERROR   ocr inference failed: PP-OCRv6_medium_det 추론에 실패했을 때. 스택은 로그에만 남긴다
# - llm/providers/modal_gpu.py
#   - WARNING image model unavailable: modal이 설치되지 않음. model, missing
#   - ERROR   modal lookup failed: 토큰이 없거나 앱을 찾지 못했을 때. reason
#   - ERROR   modal call failed: Modal 앱이 없거나, 키가 틀렸거나, 원격 추론이 실패했을 때. method, reason
# - llm/providers/slm.py
#   - WARNING image model unavailable: torch 또는 transformers가 설치되지 않음. model, missing
#   - ERROR   slm model load failed: AlexWortega/openjev 4B 로드에 실패했을 때
#   - ERROR   slm inference failed: 추론 중 예외가 났을 때. 스택은 로그에만 남긴다
#
# 이미지 바이트, base64, API 키, 모델 원문은 로그에 넣지 않는다. URL은 호스트만 남긴다.

READY = False


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
    return logger


def log_import_error(logger: logging.Logger, model: str, packages: tuple[str, ...], exc: ImportError) -> None:
    # 패키지 자체가 없으면 이 환경에서 그 장치를 쓰지 않는 것으로 보고 WARNING 한 줄만 남긴다(v2 기본 설치).
    # 설치는 됐는데 하위 의존성이 없거나 import가 깨졌으면 고쳐야 하는 문제라 ERROR와 스택을 남긴다.
    missing = (getattr(exc, "name", None) or "").split(".")[0]
    if isinstance(exc, ModuleNotFoundError) and missing in packages:
        logger.warning("image model unavailable: model=%s missing=%s", model, missing)
    else:
        logger.exception("image model import failed: model=%s missing=%s", model, missing or type(exc).__name__)
