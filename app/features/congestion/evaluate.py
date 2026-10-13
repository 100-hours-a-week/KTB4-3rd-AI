"""고정 합성 데이터의 실모델 품질과 TestClient HTTP 시간을 측정한다."""

import argparse
import hashlib
import json
import logging
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from threading import Lock
from time import perf_counter
from typing import Any
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from .decisions import DecisionsRelevanceScorer
from .evaluation import score_load, score_quality
from .extractor import call_api_model
from .models import CongestionAnalysisRequest
from .preview import diagnose_spot
from .relevance import RelevancePolicy, RelevanceScorer

ROOT = Path(__file__).resolve().parents[3]


def save(path: Path, value: Any) -> None:
    """중단 전까지 완료한 행을 임시 파일에서 교체해 보존한다."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def freeze(dataset: Path, output: Path, settings: dict[str, Any]) -> dict[str, Any]:
    """정본 해시를 검사하고 새 실행 폴더에 입력·코드·설정을 고정한다."""
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    inputs = {}
    for name, expected in manifest["sha256"].items():
        data = (dataset / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"dataset hash mismatch: {name}")
        inputs[name] = data
    paths = [
        *ROOT.glob("app/features/congestion/*.py"),
        ROOT / "app/llm/providers/openai.py",
        ROOT / "app/main.py",
        ROOT / "app/api/router.py",
        ROOT / "tests/test_congestion_evaluation.py",
    ]
    metadata = {
        "started_at": datetime.now(UTC).isoformat(),
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "files_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths
        },
        "dataset_sha256": manifest["sha256"],
        "settings": settings,
        "data": "synthetic",
        "load_transport": "FastAPI TestClient (in-process HTTP)",
        "load_concurrency": "one request at a time, at most 3 spots inside a request",
        "completed": False,
    }
    output.mkdir(parents=True, exist_ok=False)
    for name, data in inputs.items():
        (output / name).write_bytes(data)
    for path in paths:
        # 보관한 테스트 사본이 pytest 수집 대상이 되지 않도록 텍스트로 저장한다.
        snapshot = output / "source" / (str(path.relative_to(ROOT)) + ".txt")
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(path.read_bytes())
    save(output / "metadata.json", metadata)
    return metadata


class ModelCalls:
    """평가 프로세스 안에서 API 경로·상태·시간·사용량만 관측한다."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.lock = Lock()
        self.original = httpx.Client.send

    def send(
        self, client: httpx.Client, request: httpx.Request, *args: Any, **kwargs: Any
    ) -> httpx.Response:
        if request.url.host != "api.openai.com":
            return self.original(client, request, *args, **kwargs)
        started = perf_counter()
        row: dict[str, Any] = {"endpoint": request.url.path, "status": None}
        try:
            response = self.original(client, request, *args, **kwargs)
            row["status"] = response.status_code
            try:
                body = response.json()
                row["model"], row["usage"] = body.get("model"), body.get("usage")
            except (ValueError, AttributeError):
                row["usage"] = None
            return response
        except Exception as exc:
            row["error_type"] = type(exc).__name__
            raise
        finally:
            row["seconds"] = round(perf_counter() - started, 6)
            with self.lock:
                self.rows.append(row)


def _errors(exc: BaseException) -> list[str]:
    names = []
    while exc is not None:
        names.append(type(exc).__name__)
        exc = exc.__cause__  # type: ignore[assignment]
    return names


def run_quality(
    cases: list[dict[str, Any]],
    output: Path,
    score: RelevanceScorer,
    complete: Callable[[str], str],
    policy: RelevancePolicy,
) -> list[dict[str, Any]]:
    """기술 실패를 저장하고 다음 사례로 진행하며 매번 전체 정답 분모로 채점한다."""
    rows = []
    calls = ModelCalls()
    with patch.object(
        httpx.Client,
        "send",
        lambda client, request, **kw: calls.send(client, request, **kw),
    ):
        for case in cases:
            started, offset = perf_counter(), len(calls.rows)
            row: dict[str, Any] = {"case_id": case["id"], "success": False}
            try:
                request = CongestionAnalysisRequest.model_validate(case["request"])
                row["observed"] = diagnose_spot(request, score, complete, policy)
                row["success"] = True
            except Exception as exc:  # noqa: BLE001 - 사례 실패를 분모에서 빼지 않고 보존한다.
                row["error_types"] = _errors(exc)
            row["elapsed_seconds"] = round(perf_counter() - started, 6)
            row["calls"] = calls.rows[offset:]
            rows.append(row)
            save(output / "quality-results.json", rows)
            save(output / "quality-summary.json", score_quality(cases, rows))
            print(
                json.dumps(
                    {
                        "case_id": case["id"],
                        "success": row["success"],
                        "seconds": row["elapsed_seconds"],
                    }
                ),
                flush=True,
            )
    return rows


def summarize_load(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {}
    for size in sorted({row["signal_count"] for row in rows}):
        selected = [row for row in rows if row["signal_count"] == size]
        seconds = [row["elapsed_seconds"] for row in selected]
        summary[str(size)] = {
            "runs": len(selected),
            "min_seconds": min(seconds),
            "median_seconds": median(seconds),
            "max_seconds": max(seconds),
            "failed_requests": sum(not row["success"] for row in selected),
            "success_spots": sum(row["success_spots"] for row in selected),
            "exact_spots": sum(row["exact_spots"] for row in selected),
            "total_spots": sum(row["total_spots"] for row in selected),
            "calls_per_run": [len(row["calls"]) for row in selected],
        }
    return summary


def run_load(
    cases: list[dict[str, Any]], output: Path, repeats: int
) -> list[dict[str, Any]]:
    """실제 HTTP 엔드포인트를 순차 실행하며 실패·집계 불일치가 나면 확대를 멈춘다."""
    from ...main import app

    rows = []
    calls = ModelCalls()
    with (
        patch.object(
            httpx.Client,
            "send",
            lambda client, request, **kw: calls.send(client, request, **kw),
        ),
        TestClient(app) as client,
    ):
        for case in cases:
            for repeat in range(1, repeats + 1):
                started, offset = perf_counter(), len(calls.rows)
                row: dict[str, Any] = {
                    "case_id": case["id"],
                    "signal_count": case["signal_count"],
                    "repeat": repeat,
                    "transport": "TestClient",
                }
                try:
                    response = client.post("/congestion-analyses", json=case["request"])
                    row["status"] = response.status_code
                    row["response"] = response.json()
                    row.update(score_load(case, row["response"], response.status_code))
                except Exception as exc:  # noqa: BLE001 - HTTP 측정 실패도 결과로 보존한다.
                    row["error_types"] = _errors(exc)
                    row.update(
                        success=False,
                        success_spots=0,
                        exact_spots=0,
                        total_spots=len(case["expected_results"]),
                    )
                row["elapsed_seconds"] = round(perf_counter() - started, 6)
                row["calls"] = calls.rows[offset:]
                rows.append(row)
                save(output / "load-results.json", rows)
                save(output / "load-summary.json", summarize_load(rows))
                print(
                    json.dumps(
                        {
                            key: value
                            for key, value in row.items()
                            if key not in ("response", "calls")
                        }
                    ),
                    flush=True,
                )
                if not row["success"] or row["exact_spots"] != row["total_spots"]:
                    return rows
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="혼잡도 합성 데이터 실모델 평가")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=["quality", "load"], required=True)
    parser.add_argument("--sizes", type=int, nargs="+", default=[30, 120])
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1 or len(args.sizes) != len(set(args.sizes)):
        parser.error("반복은 양수, 입력량은 중복 없이 지정하세요")
    if not os.environ.get("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY가 필요합니다")
    settings = {
        name: float(os.environ[name])
        for name in (
            "CONGESTION_RELEVANCE_EXCLUDE_BELOW",
            "CONGESTION_RELEVANCE_RELATED_AT_OR_ABOVE",
            "CONGESTION_DECISIONS_TIMEOUT_SECONDS",
        )
    }
    policy = RelevancePolicy(
        settings["CONGESTION_RELEVANCE_EXCLUDE_BELOW"],
        settings["CONGESTION_RELEVANCE_RELATED_AT_OR_ABOVE"],
    )
    scorer = DecisionsRelevanceScorer(
        timeout=settings["CONGESTION_DECISIONS_TIMEOUT_SECONDS"]
    )
    load = json.loads((args.dataset / "load.json").read_text(encoding="utf-8"))
    selected = [case for case in load if case["signal_count"] in args.sizes]
    if args.mode == "load" and len(selected) != len(args.sizes):
        parser.error("데이터셋에 없는 입력량입니다")
    metadata = freeze(
        args.dataset,
        args.output,
        {**settings, "mode": args.mode, "sizes": args.sizes, "repeats": args.repeats},
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.mode == "quality":
        cases = json.loads((args.output / "quality.json").read_text(encoding="utf-8"))
        rows = run_quality(cases, args.output, scorer, call_api_model, policy)
        metadata["completed"] = len(rows) == len(cases)
    else:
        rows = run_load(
            sorted(selected, key=lambda case: case["signal_count"]),
            args.output,
            args.repeats,
        )
        metadata["completed"] = len(rows) == len(selected) * args.repeats
        metadata["stopped_on_failure"] = any(
            not row["success"] or row["exact_spots"] != row["total_spots"]
            for row in rows
        )
    metadata["finished_at"] = datetime.now(UTC).isoformat()
    save(args.output / "metadata.json", metadata)
    return (
        0
        if metadata["completed"] and not metadata.get("stopped_on_failure", False)
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
