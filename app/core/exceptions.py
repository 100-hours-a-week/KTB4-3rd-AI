# 클라이언트에 주는 message는 고정 문구다. 호스트, 상태 코드, 바이트 수는 로그에만 남긴다.
#
# 422 REQUEST_INVALID      요청 필드가 없음
# 422 IMAGE_FETCH_FAILED   이미지 다운로드 실패
# 422 IMAGE_SIZE_INVALID   비었거나 5MB 초과
# 422 IMAGE_TYPE_INVALID   jpeg, png가 아님
# 422 JUDGE_PARSE_FAILED   모델 출력이 JSON이 아니거나 필요한 값이 없음
# 502 UPSTREAM_FAILED      OpenAI 호출 실패 또는 응답 형식이 다름
# 503 MODEL_NOT_READY      키, 가중치, 패키지가 없음. message에 api, ocr, open, modal 중 어디인지 적는다.
# 500 INTERNAL             등록되지 않은 모델이거나 추론 중 예외


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


class InternalError(AppError):
    def __init__(self, message: str = "internal error") -> None:
        super().__init__("INTERNAL", message, 500)
