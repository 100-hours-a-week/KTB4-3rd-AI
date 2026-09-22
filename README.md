# KTB4 3rd AI

## Local setup

Python 3.12 and `uv` are required.

```bash
cp .env.example .env
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

The congestion analysis cache is process-local, so the API must run with exactly one worker
until an external cache such as Redis is introduced.

## Checks

```bash
uv run pytest
uv run ruff check .
uv run mypy app/core app/api app/features/congestion app/main.py
```
