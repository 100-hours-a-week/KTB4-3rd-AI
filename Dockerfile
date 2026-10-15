# syntax=docker/dockerfile:1
# 모여타 AI(FastAPI) 배포 이미지 — V2 ECS `fastapi` 서비스
#
# 빌드:  docker build -t moyeota-ai .
#        (V3) 로컬 모델: --build-arg EXTRAS=local / Modal: --build-arg EXTRAS=modal
# 실행:  docker run --env-file .env -p 8000:8000 moyeota-ai
#
# - 런타임 값(OPENAI_API_KEY 등)은 이미지에 넣지 않는다. ECS 태스크가 SSM(/moyeota/v2/<stage>/ai/*)에서 주입한다.
# - IMAGE_DEVICE를 넣지 않으면 off(V2, luna만 사용)로 동작한다.
# - 포트 8000은 ALB 대상 그룹·Service Connect(http://fastapi:8000)·CI 헬스체크와 맞춰 둔 값이다.

# 배포 전에 검토한 digest로 고정한다 (CI의 PYTHON_VERSION 3.12와 맞춘다)
#   docker buildx imagetools inspect python:3.12-slim   → Digest: sha256:...
ARG PYTHON_IMAGE=python:3.12-slim

# -----------------------------------------------------------------------------
# build: 가상환경에 의존성 설치
# -----------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS build

ARG EXTRAS=""
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

COPY pyproject.toml ./
COPY app ./app
RUN pip install ".${EXTRAS:+[$EXTRAS]}"

# -----------------------------------------------------------------------------
# runtime: 가상환경 + 앱 소스만, 비루트 실행
# -----------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home app

WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --chown=app:app app ./app

USER app
EXPOSE 8000

# - 워커 1개: 태스크가 0.5 vCPU라 프로세스를 늘리지 않고, sync 핸들러는 uvicorn 스레드풀에서 처리한다.
# - graceful shutdown 50초: ECS stopTimeout(60초) 안에 진행 중인 OpenAI 호출(최대 60초 대기)을 최대한 마무리한다.
# - exec 형태라 uvicorn이 PID 1로 SIGTERM을 직접 받는다.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-graceful-shutdown", "50"]
