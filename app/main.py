from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api.router import router
from .core.config import IMAGE_DEVICE, LOCAL_DEVICES
from .core.exceptions import AppError, InternalError, RequestInvalid
from .core.logging import get_logger
from .llm import load

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 이 서버에서 모델을 돌릴 때만 기동 시 미리 올린다. 실패하면 엔진이 로그를 남기고, 요청은 luna가 처리한다.
    if IMAGE_DEVICE in LOCAL_DEVICES:
        for name in ("ocr", "slm"):
            try:
                load(name, IMAGE_DEVICE)
            except AppError:
                pass
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(router)


def error(exc: AppError) -> JSONResponse:
    body = {"success": False, "code": exc.code, "message": exc.message}
    return JSONResponse(body, status_code=exc.status_code)


@app.exception_handler(AppError)
async def app_error(request, exc: AppError) -> JSONResponse:
    return error(exc)


@app.exception_handler(RequestValidationError)
async def request_invalid(request, exc: RequestValidationError) -> JSONResponse:
    return error(RequestInvalid())


@app.exception_handler(Exception)
async def unhandled(request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error")
    return error(InternalError())
