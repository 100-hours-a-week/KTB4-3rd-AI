# 클라이언트에 주는 message는 고정 문구다. 호스트, 상태 코드, 바이트 수는 로그에만 남긴다.
#
# 422 REQUEST_INVALID      요청 필드가 없음
# 422 IMAGE_FETCH_FAILED   이미지 다운로드 실패. 잘못된 URL, 5초 초과 포함
# 422 IMAGE_SIZE_INVALID   비었거나 5MB 초과
# 422 IMAGE_TYPE_INVALID   jpeg, png가 아님
# 422 JUDGE_PARSE_FAILED   모델 출력이 JSON이 아니거나 필요한 값이 없음. 영수증이 이체 완료가 아닐 때도 여기로 온다
# 502 UPSTREAM_FAILED      OpenAI 호출 실패 또는 응답 형식이 다름
# 503 MODEL_NOT_READY      키, 가중치, 패키지가 없음. api, ocr, open, modal 중 어디인지는 로그에만 남긴다. 서버 종료 중 받은 요청도 여기로 온다
# 504 QUEUE_TIMEOUT        내부 큐에서 기다리다 대기 상한(60초)을 넘김
# 500 INTERNAL             등록되지 않은 모델이거나 추론 중 예외


# 오류 응답 로그(request failed)에 붙는 확인 안내. 같은 request_id의 앞선 로그에 구체 원인이 있다.
HINTS = {
    "REQUEST_INVALID": "요청 JSON에 문자열 image_URL이 있는지 확인",
    "IMAGE_FETCH_FAILED": "image_URL이 5초 안에 리다이렉트 없이 200을 주는지 확인(presigned 만료, 권한, 주소)",
    "IMAGE_SIZE_INVALID": "이미지가 1바이트 이상 5MB 이하인지 확인",
    "IMAGE_TYPE_INVALID": "파일 내용이 JPEG 또는 PNG인지 확인(확장자가 아니라 내용)",
    "JUDGE_PARSE_FAILED": "사진에서 값을 확정하지 못함. 사진 품질과, 영수증은 이체 완료 화면인지 확인",
    "UPSTREAM_FAILED": "OpenAI 또는 Modal 호출 실패. 같은 request_id의 openai/modal 로그에서 reason 확인",
    "MODEL_NOT_READY": "OPENAI_API_KEY_1(또는 OPENAI_API_KEY)이 컨테이너에 주입됐는지 확인",
    "QUEUE_TIMEOUT": "요청이 몰려 대기 상한(60초)을 넘김. 같은 시각 queue job expired 로그 확인",
    "INTERNAL": "처리되지 않은 예외. 같은 request_id의 Traceback 확인",
}


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class RequestInvalid(AppError):
    def __init__(self, message: str = "request is invalid") -> None:
        super().__init__("REQUEST_INVALID", message, 422)


class ImageFetchError(AppError):
    def __init__(self, message: str = "image download failed") -> None:
        super().__init__("IMAGE_FETCH_FAILED", message, 422)


class ImageSizeError(AppError):
    def __init__(self, message: str = "image size is invalid") -> None:
        super().__init__("IMAGE_SIZE_INVALID", message, 422)


class ImageTypeError(AppError):
    def __init__(self, message: str = "image must be jpeg or png") -> None:
        super().__init__("IMAGE_TYPE_INVALID", message, 422)


class JudgeParseError(AppError):
    def __init__(self, message: str = "model output is not valid") -> None:
        super().__init__("JUDGE_PARSE_FAILED", message, 422)


class UpstreamError(AppError):
    def __init__(self, message: str = "upstream request failed") -> None:
        super().__init__("UPSTREAM_FAILED", message, 502)


class ModelNotReady(AppError):
    def __init__(self, message: str = "model is not ready") -> None:
        super().__init__("MODEL_NOT_READY", message, 503)


class QueueTimeout(AppError):
    def __init__(self, message: str = "queue wait timed out") -> None:
        super().__init__("QUEUE_TIMEOUT", message, 504)


class InternalError(AppError):
    def __init__(self, message: str = "internal error") -> None:
        super().__init__("INTERNAL", message, 500)
