"""단일 메시지 계약과 Decisions·스팟 판정의 HTTP 연결을 검증한다."""

import json
import logging
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


def test_http_runs_decisions_spot_extraction_aggregation_and_code_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_path = Path(__file__).parent / "fixtures/congestion/spot-preview.json"
    case = json.loads(case_path.read_text())
    data = case["request"]
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
                    {
                        "type": "predicate",
                        "name": item["name"],
                        "probability": scores[item["name"]],
                    }
                    for item in reversed(body["questions"])
                ]
            },
        )

    def complete(prompt: str) -> str:
        extraction_inputs.append(json.loads(prompt.split("입력 JSON:\n", 1)[1]))
        return json.dumps(case["model_output"])

    analyzer = LLMCongestionAnalyzer(
        score=DecisionsRelevanceScorer(transport=httpx.MockTransport(respond)),
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
    assert extraction_inputs[0]["window_end"] == data["window_end"]
    for request in decision_requests:
        assert json.loads(request["input"])["window_end"] == data["window_end"]
    assert extraction_inputs[0]["targets"] == [
        {"signal_type": "post", "signal_id": 1},
        {"signal_type": "comment", "signal_id": 1},
        {"signal_type": "post", "signal_id": 3},
    ]
    assert {
        key: value
        for key, value in response.json().items()
        if key not in {"event_id", "schema_version", "window_end", "analyzed_at"}
    } == {
        "spot_id": 7,
        "analysis_status": "SUCCESS",
        "result": {
            "congestion_level": "LOW",
            "report_count": 1,
            "causes": [{"cause_code": "BREAKDOWN", "report_count": 1}],
            "summary": "제보 기준 혼잡도는 한산이며, 혼잡 제보자는 1명입니다. 제보된 원인은 차량·설비 고장(1명)입니다.",
        },
    }


def test_default_analyzer_uses_congestion_module_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "CONGESTION_RELEVANCE_EXCLUDE_BELOW",
        "CONGESTION_RELEVANCE_RELATED_AT_OR_ABOVE",
        "CONGESTION_DECISIONS_TIMEOUT_SECONDS",
    ):
        monkeypatch.setenv(name, "invalid-old-env-value")
    scorer, policy = LLMCongestionAnalyzer()._configured_scorer()
    assert policy == RelevancePolicy(0.2, 0.8)
    assert isinstance(scorer, DecisionsRelevanceScorer)


def test_missing_model_key_returns_failed_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY_2", raising=False)
    case_path = Path(__file__).parent / "fixtures/congestion/spot-preview.json"
    data = json.loads(case_path.read_text())["request"]
    response = TestClient(app).post("/congestion-analyses", json=data)
    assert response.status_code == 200
    assert {
        key: value
        for key, value in response.json().items()
        if key not in {"event_id", "schema_version", "window_end", "analyzed_at"}
    } == {
        "spot_id": 7,
        "analysis_status": "FAILED",
        "result": None,
        "code": "ANALYSIS_FAILED",
        "message": "analysis failed",
    }


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
    case_path = Path(__file__).parent / "fixtures/congestion/spot-preview.json"
    data = json.loads(case_path.read_text())["request"]
    data["event_id"] = "log-event-1"

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
    assert {
        key: value
        for key, value in response.json().items()
        if key not in {"event_id", "schema_version", "window_end", "analyzed_at"}
    } == {
        "spot_id": 7,
        "analysis_status": "FAILED",
        "result": None,
        "code": "ANALYSIS_FAILED",
        "message": "analysis failed",
    }
    assert "private" not in response.text
    records = [
        record for record in caplog.records if record.name == "app.features.congestion.service"
    ]
    assert len(records) == 1
    assert records[0].getMessage() == (
        f"congestion analysis failed: spot_id=7 event_id=log-event-1 error={error_type} "
        f"request_id={response.headers['X-Request-ID']}"
    )
    assert records[0].exc_info is None
