from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from .api.router import router
from .core.config import DEVICE
from .core.exceptions import HINTS, AppError, InternalError, RequestInvalid
from .core.logging import get_logger, request_context
from .core.telemetry import RequestMetrics, build_meter_provider
from .llm import load

logger = get_logger(__name__)
# OTLP push는 OTLP_METRICS_ENABLED=true일 때만 켜진다. 꺼져 있으면 None이다.
meter_provider = build_meter_provider()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 이미지 모델을 쓸 때 기동 시 미리 올린다(Modal은 토큰과 앱 확인). 실패하면 down에 들어가 요청은 바로 luna가 처리한다.
    if DEVICE != "off":
        for name in ("ocr", "slm"):
            try:
                load(name, DEVICE)
            except AppError:
                pass
    yield
    # 종료(SIGTERM) 때 아직 안 보낸 지표를 flush한다. 유예 시간 안에 끝나야 한다.
    if meter_provider is not None:
        meter_provider.shutdown()


app = FastAPI(lifespan=lifespan)
app.include_router(router)
# Cloud Prometheus가 수집하는 /metrics. 대시보드·알림이 기본 지표 이름과 라벨을 쓰므로 설정을 바꾸지 않는다.
# 헬스체크와 수집 요청은 지표에서 뺀다.
Instrumentator(excluded_handlers=["/health", "/metrics"]).instrument(app).expose(app, include_in_schema=False)
# 병행 기간 동안 같은 요청을 OTLP로도 보낸다. 이름은 /metrics와 맞춰 두었다(core/telemetry.py).
if meter_provider is not None:
    app.add_middleware(RequestMetrics, meter=meter_provider.get_meter("fastapi"), routes=app.routes)


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
