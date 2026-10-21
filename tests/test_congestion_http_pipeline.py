"""새 요청 창 계약과 Decisions·스팟 추출의 HTTP 연결을 검증한다."""

import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.features.congestion.decisions import DecisionsRelevanceScorer
from app.features.congestion.relevance import RelevanceOutputInvalid, RelevancePolicy
from app.features.congestion.service import (
    AnalysisError,
    LLMCongestionAnalyzer,
    get_analyzer,
)
from app.main import app


def request_data() -> dict[str, Any]:
    return {
        "request_id": "new-http-1",
        "as_of": "2026-10-12T12:00:00+09:00",
        "window_minutes": 60,
        "spots": [{"spot_id": 7, "post_bundles": []}],
    }


@pytest.mark.parametrize("value", [None, 0, 15, 20, 65, 10.0, 60.0, "10", True])
def test_http_rejects_invalid_window_before_analysis(value: object) -> None:
    data = request_data()
    data["window_minutes"] = value
    response = TestClient(app).post("/congestion-analyses", json=data)
    assert response.status_code == 422
    assert response.json() == {
        "success": False,
        "code": "REQUEST_INVALID",
        "message": "request is invalid",
    }


def test_http_window_is_required_without_legacy_fallback() -> None:
    data = request_data()
    del data["window_minutes"]
    response = TestClient(app).post("/congestion-analyses", json=data)
    assert response.status_code == 422


@pytest.mark.parametrize("minutes", [10, 60])
def test_http_accepts_explicit_window_for_empty_spot(minutes: int) -> None:
    data = request_data()
    data["window_minutes"] = minutes
    original = deepcopy(data)
    response = TestClient(app).post("/congestion-analyses", json=data)
    assert response.status_code == 200
    assert response.json()["spots"][0]["analysis_status"] == "SUCCESS"
    assert data == original


def test_http_runs_decisions_spot_extraction_aggregation_and_code_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_path = Path(__file__).resolve().parents[1] / "examples/congestion/spot-preview.json"
    case = json.loads(case_path.read_text())
    data = case["request"]
    data["spots"].append({"spot_id": 8, "post_bundles": []})
    scores = {
        f"{item['signal_type']}_{item['signal_id']}": item["probability"] for item in case["scores"]
    }
    monkeypatch.setenv("OPENAI_API_KEY_2", "test-key")
    decision_requests: list[dict[str, Any]] = []
    extraction_inputs: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        decision_requests.append(body)
        return httpx.Response(
            200,
            json={
                "answers": [
                    {"type": "predicate", "name": item["name"], "probability": scores[item["name"]]}
                    for item in reversed(body["questions"])
                ]
            },
        )

    def complete(prompt: str) -> str:
        extraction_inputs.append(json.loads(prompt.split("입력 JSON:\n", 1)[1]))
        return json.dumps(case["model_output"])

    analyzer = LLMCongestionAnalyzer(
        score=DecisionsRelevanceScorer(timeout=5, transport=httpx.MockTransport(respond)),
        policy=RelevancePolicy(0.2, 0.8),
        complete=complete,
    )
    app.dependency_overrides[get_analyzer] = lambda: analyzer
    try:
        response = TestClient(app).post("/congestion-analyses", json=data)
    finally:
        app.dependency_overrides.pop(get_analyzer, None)
    assert response.status_code == 200
    assert len(decision_requests) == 3
    assert len(extraction_inputs) == 1
    assert extraction_inputs[0]["targets"] == [
        {"signal_type": "post", "signal_id": 1},
        {"signal_type": "comment", "signal_id": 1},
        {"signal_type": "post", "signal_id": 3},
    ]
    assert response.json()["spots"] == [
        {
            "spot_id": 7,
            "analysis_status": "SUCCESS",
            "result": {
                "congestion_level": "LOW",
                "report_count": 1,
                "causes": [{"cause_code": "BREAKDOWN", "report_count": 1}],
                "summary": "제보 기준 혼잡도는 한산이며, 혼잡 제보자는 1명입니다. "
                "제보된 원인은 차량·설비 고장(1명)입니다.",
            },
        },
        {
            "spot_id": 8,
            "analysis_status": "SUCCESS",
            "result": {
                "congestion_level": "UNKNOWN",
                "report_count": 0,
                "causes": [],
                "summary": "혼잡 단계를 판단할 유효 제보가 없습니다.",
            },
        },
    ]


@pytest.mark.parametrize("config", ["missing", "invalid"])
def test_missing_or_invalid_relevance_settings_fail_only_nonempty_spot(
    monkeypatch: pytest.MonkeyPatch,
    config: str,
) -> None:
    for name in (
        "CONGESTION_RELEVANCE_EXCLUDE_BELOW",
        "CONGESTION_RELEVANCE_RELATED_AT_OR_ABOVE",
        "CONGESTION_DECISIONS_TIMEOUT_SECONDS",
        "OPENAI_API_KEY_2",
    ):
        monkeypatch.delenv(name, raising=False)
    if config == "invalid":
        monkeypatch.setenv("CONGESTION_RELEVANCE_EXCLUDE_BELOW", "not-a-probability")
    case_path = Path(__file__).resolve().parents[1] / "examples/congestion/spot-preview.json"
    data = json.loads(case_path.read_text())["request"]
    data["spots"].append({"spot_id": 8, "post_bundles": []})
    response = TestClient(app).post("/congestion-analyses", json=data)
    assert response.status_code == 200
    assert response.json()["spots"][0] == {
        "spot_id": 7,
        "analysis_status": "FAILED",
        "result": None,
        "code": "ANALYSIS_FAILED",
        "message": "analysis failed",
    }
    assert response.json()["spots"][1]["analysis_status"] == "SUCCESS"


@pytest.mark.parametrize(
    ("error", "error_type"),
    [
        (RelevanceOutputInvalid("private provider details"), "RelevanceOutputInvalid"),
        (AnalysisError("private provider details"), "AnalysisError"),
    ],
)
def test_analysis_failure_logs_only_identifiers_and_type_without_calling_extraction(
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    error_type: str,
) -> None:
    case_path = Path(__file__).resolve().parents[1] / "examples/congestion/spot-preview.json"
    data = json.loads(case_path.read_text())["request"]
    data["request_id"] = "log-request-1\nforged"
    data["spots"].append({"spot_id": 8, "post_bundles": []})

    def fail(_prepared: Any) -> dict[Any, float]:
        raise error

    def unexpected(_prompt: str) -> str:
        pytest.fail("관련성 실패 후 추출을 호출함")

    analyzer = LLMCongestionAnalyzer(
        score=fail, policy=RelevancePolicy(0.2, 0.8), complete=unexpected
    )
    app.dependency_overrides[get_analyzer] = lambda: analyzer
    try:
        with caplog.at_level(logging.WARNING, logger="app.features.congestion.service"):
            response = TestClient(app).post("/congestion-analyses", json=data)
    finally:
        app.dependency_overrides.pop(get_analyzer, None)
    assert response.status_code == 200
    assert response.json()["spots"][0] == {
        "spot_id": 7,
        "analysis_status": "FAILED",
        "result": None,
        "code": "ANALYSIS_FAILED",
        "message": "analysis failed",
    }
    assert response.json()["spots"][1]["analysis_status"] == "SUCCESS"
    assert "private" not in response.text
    records = [record for record in caplog.records if record.name == "app.features.congestion.service"]
    assert len(records) == 1
    assert records[0].getMessage() == (
        "congestion analysis failed: request_id='log-request-1\\nforged' "
        f"spot_id=7 error_type={error_type}"
    )
    assert records[0].exc_info is None
