# KTB4 3rd AI

## Local setup

Python 3.12 and `uv` are required.

```bash
cp .env.example .env
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Run one worker for this project setup. The current congestion HTTP analysis recalculates each
request and does not use the old process-local congestion cache.

Spring 연동 요청·응답과 미합의 운영 항목은 [혼잡도 연동 제안](docs/congestion/spring-integration.md)에
정리했다.
현재 진행 상황과 다음 작업은 [혼잡도 한눈에 보기](docs/congestion/progress-at-a-glance.html)에
정리했다.

## Checks

```bash
uv run pytest
uv run ruff check .
uv run mypy app/core app/api app/features/congestion app/main.py
```
