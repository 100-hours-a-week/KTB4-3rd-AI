import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from .api.router import router
from .core.config import DEVICE, LOCAL_DEVICES
from .core.exceptions import HINTS, AppError, InternalError, RequestInvalid
from .core.logging import get_logger, request_context
from .features.receipt import main as receipt
from .features.taxi_meter import main as taxi_meter
from .inner_queue import Scheduler
from .llm import load, prepare_modal

logger = get_logger(__name__)


def preload(device: str) -> None:
    # 로컬 장치는 가중치를 미리 올린다. 실패하면 down에 들어가 GPU 큐 요청도 바로 luna가 처리한다.
    for name in ("ocr", "slm"):
        try:
            load(name, device)
        except AppError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 요청은 모두 내부 큐(전체 30 / API 20 / GPU 10)를 거친다. 연결은 여기서만 한다.
    scheduler = Scheduler({
        "taxi_meter": (taxi_meter.run_api, taxi_meter.run_gpu),
        "receipt": (receipt.run_api, receipt.run_gpu),
    })
    await scheduler.start()
    app.state.scheduler = scheduler
    # 이미지 모델 준비는 기동을 막지 않는다. 준비 중이면 GPU 큐 요청도 luna가 처리한다.
    # Modal은 버전 이름 앱을 찾고 없으면 배포한다(첫 배포는 몇 분 걸린다).
    prepare = None
    if DEVICE == "modal":
        prepare = asyncio.create_task(prepare_modal())
    elif DEVICE in LOCAL_DEVICES:
        prepare = asyncio.create_task(asyncio.to_thread(preload, DEVICE))
    yield
    await scheduler.stop(50)  # --timeout-graceful-shutdown 50과 맞춘다
    if prepare is not None:
        prepare.cancel()


app = FastAPI(lifespan=lifespan)
app.include_router(router)
# Cloud Prometheus가 수집하는 /metrics. 대시보드·알림이 기본 지표 이름과 라벨을 쓰므로 설정을 바꾸지 않는다.
# 헬스체크와 수집 요청은 지표에서 뺀다.
Instrumentator(excluded_handlers=["/health", "/metrics"]).instrument(app).expose(app, include_in_schema=False)


@app.middleware("http")
async def timing(request, call_next):
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        ms = int((time.perf_counter() - started) * 1000)
        fallback = "OK" if status < 400 else "INTERNAL" if status == 500 else f"HTTP_{status}"
        code = getattr(request.state, "code", fallback)
        logger.info("request done: path=%s status=%s code=%s ms=%s", request.url.path, status, code, ms)


# 요청마다 request_id를 정해 로그에 붙인다. 가장 바깥 미들웨어가 되도록 다른 미들웨어보다 뒤에 등록한다.
app.middleware("http")(request_context)


def error(request, exc: AppError) -> JSONResponse:
    log = logger.error if exc.status_code >= 500 else logger.warning
    log("request failed: path=%s status=%s code=%s hint=%s", request.url.path, exc.status_code, exc.code, HINTS.get(exc.code, "-"))
    body = {"success": False, "code": exc.code, "message": exc.message}
    return JSONResponse(body, status_code=exc.status_code)


@app.exception_handler(AppError)
async def app_error(request, exc: AppError) -> JSONResponse:
    request.state.code = exc.code
    return error(request, exc)


@app.exception_handler(RequestValidationError)
async def request_invalid(request, exc: RequestValidationError) -> JSONResponse:
    fields = [".".join(map(str, item["loc"])) for item in exc.errors()]
    logger.warning("request is invalid: path=%s fields=%s", request.url.path, fields)
    invalid = RequestInvalid()
    request.state.code = invalid.code
    return error(request, invalid)


@app.exception_handler(Exception)
async def unhandled(request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error: path=%s error=%s", request.url.path, type(exc).__name__)
    return error(request, InternalError())
