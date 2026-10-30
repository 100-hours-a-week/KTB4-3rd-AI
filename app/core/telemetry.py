import os
import socket
import time
from collections.abc import Mapping

import httpx
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from starlette.routing import Match

# OTLP push로 보내는 요청 지표. /metrics(prometheus_fastapi_instrumentator)와 같은 이름이 나오도록 맞춘다.
# 수신 측(Prometheus)은 counter에 _total을, unit "s"인 histogram에 _seconds를 붙인다.
# 그래서 instrument 이름은 접미사 없이 http_requests, http_request_duration으로 둔다.
REQUEST_COUNTER = "http_requests"
REQUEST_DURATION = "http_request_duration"
DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
# 헬스체크와 수집 요청은 지표에서 뺀다 (/metrics 쪽과 같은 기준).
EXCLUDED_PATHS = ("/health", "/metrics")


def service_instance_id(env: Mapping[str, str]) -> str:
    # ECS 태스크 ID는 ECS_TASK_ID가 있으면 그 값을, 없으면 task metadata v4의 TaskARN 끝 값을 쓴다.
    # metadata를 못 읽으면 HOSTNAME으로 대체한다.
    if env.get("ECS_TASK_ID"):
        return env["ECS_TASK_ID"]
    metadata_uri = env.get("ECS_CONTAINER_METADATA_URI_V4", "").strip()
    if metadata_uri:
        try:
            res = httpx.get(f"{metadata_uri}/task", timeout=1.0)
            res.raise_for_status()
            body = res.json()
            task_arn = body.get("TaskARN", "") if isinstance(body, dict) else ""
            task_id = task_arn.rsplit("/", 1)[-1]
            if task_id:
                return task_id
        except (httpx.HTTPError, ValueError):
            pass
    return socket.gethostname()


def build_meter_provider(env: Mapping[str, str] = os.environ) -> MeterProvider | None:
    # OTLP_METRICS_ENABLED=true 이고 OTLP_METRICS_URL이 있을 때만 push한다. 아니면 None이고 push 코드는 붙지 않는다.
    endpoint = env.get("OTLP_METRICS_URL", "").strip()
    if env.get("OTLP_METRICS_ENABLED", "").strip().lower() != "true" or not endpoint:
        return None

    attributes = {
        "service.name": env.get("OTLP_SERVICE_NAME", "fastapi"),
        "service.instance.id": service_instance_id(env),
        "deployment.environment": env.get("STAGE", "dev"),
        # pull 라벨과 맞춘다 (Prometheus가 stage·service 라벨로 승격한다).
        "stage": env.get("STAGE", "dev"),
        "service": env.get("OTLP_SERVICE_NAME", "fastapi"),
    }
    namespace = env.get("OTLP_SERVICE_NAMESPACE", "").strip()
    if namespace:
        attributes["service.namespace"] = namespace

    # 종료 직전 마지막 값을 보내려고 주기는 30초로 두고, 종료 때 shutdown()으로 한 번 더 보낸다.
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=endpoint, timeout=5),
        export_interval_millis=30_000,
    )
    return MeterProvider(
        resource=Resource.create(attributes),
        metric_readers=[reader],
        views=[
            View(
                instrument_name=REQUEST_DURATION,
                aggregation=ExplicitBucketHistogramAggregation(DURATION_BUCKETS),
            )
        ],
    )


class RequestMetrics:
    # 요청 수와 지연을 OTel instrument로 기록하는 ASGI 미들웨어. routes는 handler 라벨(라우트 템플릿)을 찾는 데 쓴다.
    def __init__(self, app, meter, routes):
        self.app = app
        self.routes = routes
        self.requests = meter.create_counter(REQUEST_COUNTER, description="HTTP 요청 수")
        self.duration = meter.create_histogram(REQUEST_DURATION, unit="s", description="HTTP 요청 지연")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in EXCLUDED_PATHS:
            await self.app(scope, receive, send)
            return

        status = 500

        async def send_with_status(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        started = time.perf_counter()
        try:
            await self.app(scope, receive, send_with_status)
        finally:
            attributes = {
                "handler": self._handler(scope),
                "method": scope["method"],
                "status": f"{status // 100}xx",
            }
            self.requests.add(1, attributes)
            self.duration.record(time.perf_counter() - started, attributes)

    def _handler(self, scope) -> str:
        for route in self.routes:
            match, _ = route.matches(scope)
            if match == Match.FULL:
                return route.path
        return "none"
