from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api.router import router
from .core.config import IMAGE_DEVICE
from .core.exceptions import HINTS, AppError, InternalError, RequestInvalid
from .core.logging import get_logger, request_context
from .llm import load

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 이미지 모델을 쓸 때 기동 시 미리 올린다(Modal은 토큰과 앱 확인). 실패하면 down에 들어가 요청은 바로 luna가 처리한다.
    if IMAGE_DEVICE != "off":
        for name in ("ocr", "slm"):
            try:
                load(name, IMAGE_DEVICE)
            except AppError:
                pass
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(router)


# 요청마다 request_id를 정해 로그에 붙인다. 가장 바깥 미들웨어가 되도록 다른 미들웨어보다 뒤에 등록한다.
app.middleware("http")(request_context)


def error(request, exc: AppError) -> JSONResponse:
    log = logger.error if exc.status_code >= 500 else logger.warning
    log("request failed: path=%s status=%s code=%s hint=%s", request.url.path, exc.status_code, exc.code, HINTS.get(exc.code, "-"))
    body = {"success": False, "code": exc.code, "message": exc.message}
    return JSONResponse(body, status_code=exc.status_code)


@app.exception_handler(AppError)
async def app_error(request, exc: AppError) -> JSONResponse:
    return error(request, exc)


@app.exception_handler(RequestValidationError)
async def request_invalid(request, exc: RequestValidationError) -> JSONResponse:
    return error(request, RequestInvalid())


@app.exception_handler(Exception)
async def unhandled(request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error: path=%s error=%s", request.url.path, type(exc).__name__)
    return error(request, InternalError())
