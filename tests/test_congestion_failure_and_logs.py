"""단계별 HTTP 실패와 요청 안팎의 분석 로그를 검증한다."""

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.logging import request_id
from app.features.congestion.decisions import DecisionsRelevanceScorer
from app.features.congestion.models import CongestionAnalysisMessage
from app.features.congestion.relevance import RelevancePolicy
from app.features.congestion.service import (
    LLMCongestionAnalyzer,
    analyze_message,
    get_analyzer,
)
from app.main import app


@pytest.mark.parametrize(
    ("stage", "case", "code", "reason"),
    [
        ("relevance", "timeout", "CONGESTION_DECISIONS_FAILED", "timeout"),
        ("relevance", "connect", "CONGESTION_DECISIONS_FAILED", "connect"),
        ("relevance", "401", "CONGESTION_DECISIONS_FAILED", "http_4xx"),
        ("relevance", "503", "CONGESTION_DECISIONS_FAILED", "http_5xx"),
        ("relevance", "not_json", "CONGESTION_DECISIONS_FAILED", "not_json"),
        ("relevance", "id_mismatch", "CONGESTION_DECISIONS_FAILED", "id_mismatch"),
        (
            "relevance",
            "bad_probability",
            "CONGESTION_DECISIONS_FAILED",
            "bad_probability",
        ),
        ("judge", "not_json", "CONGESTION_PARSE_FAILED", "not_json"),
        ("judge", "id_mismatch", "CONGESTION_PARSE_FAILED", "id_mismatch"),
    ],
)
def test_stage_http_failure_and_reason(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    stage: str,
    case: str,
    code: str,
    reason: str,
) -> None:
    data = json.loads(
        (Path(__file__).parent / "fixtures/congestion/spot-preview.json").read_text()
    )
    monkeypatch.setenv("OPENAI_API_KEY_2", "test-key")
    monkeypatch.setattr(
        "app.features.congestion.model_client.time.sleep", lambda _: None
    )

    def respond(request: httpx.Request) -> httpx.Response:
        if case == "timeout":
            raise httpx.ReadTimeout("private body", request=request)
        if case == "connect":
            raise httpx.ConnectError("private body", request=request)
        if case.isdigit():
            return httpx.Response(int(case), json={"error": {"type": "api_error"}})
        if case == "not_json":
            return httpx.Response(200, text="private body")
        questions = json.loads(request.content)["questions"]
        return httpx.Response(
            200,
            json={
                "answers": [
                    {
                        "type": "predicate",
                        "name": q["name"],
                        "probability": 2 if case == "bad_probability" else 0.9,
                    }
                    for q in (questions if case != "id_mismatch" else [])
                ]
            },
        )

    def related(prepared: Any) -> dict[Any, float]:
        return {(c.signal_type, c.signal_id): 0.9 for c in prepared.candidates}

    analyzer = LLMCongestionAnalyzer(
        score=DecisionsRelevanceScorer(transport=httpx.MockTransport(respond))
        if stage == "relevance"
        else related,
        policy=RelevancePolicy(0.2, 0.8),
        complete=lambda _: "private body" if case == "not_json" else '{"signals": []}',
    )
    app.dependency_overrides[get_analyzer] = lambda: analyzer
    try:
        response = TestClient(app).post(
            "/congestion-analyses",
            json=data["request"],
            headers={"X-Request-ID": "http-test"},
        )
    finally:
        app.dependency_overrides.pop(get_analyzer, None)
    assert response.status_code == 502
    assert response.json()["code"] == code
    assert "private" not in response.text
    head = (
        "congestion decisions failed"
        if stage == "relevance"
        else "congestion judge invalid"
    )
    logs = [r.getMessage() for r in caplog.records if r.getMessage().startswith(head)]
    assert len(logs) == 1
    assert f"reason={reason}" in logs[0]
    assert logs[0].endswith(" request_id=http-test")
    assert "private" not in logs[0]
    if stage == "relevance":
        assert f"status={case if case.isdigit() else '-'}" in logs[0]
        assert re.search(r"error=\S+ ms=\d+", logs[0])
    failed = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("congestion analysis failed:")
    ]
    assert len(failed) == 1
    assert f"code={code}" in failed[0]
    assert failed[0].endswith(" request_id=http-test")


@pytest.mark.parametrize(
    "mode", ["success", "no_candidates", "all_unrelated", "failure"]
)
@pytest.mark.parametrize("existing_id", [None, "consumer-job"])
def test_analysis_logs_and_request_context(
    caplog: pytest.LogCaptureFixture,
    mode: str,
    existing_id: str | None,
) -> None:
    case = json.loads(
        (Path(__file__).parent / "fixtures/congestion/spot-preview.json").read_text()
    )
    data = case["request"]
    if mode == "no_candidates":
        for post in data["post_bundles"]:
            post["context_only"] = True
            post["comments"] = []
    message = CongestionAnalysisMessage.model_validate(data)

    def score(prepared: Any) -> dict[Any, float]:
        if mode == "no_candidates":
            pytest.fail("후보가 없는데 관련성 선별을 호출함")
        return {
            (c.signal_type, c.signal_id): 0.1 if mode == "all_unrelated" else 0.9
            for c in prepared.candidates
        }

    def complete(prompt: str) -> str:
        if mode in {"no_candidates", "all_unrelated"}:
            pytest.fail("대상이 없는데 판정을 호출함")
        if mode == "failure":
            raise RuntimeError("private details")
        targets = json.loads(prompt.split("입력 JSON:\n", 1)[1])["targets"]
        return json.dumps(
            {
                "signals": [
                    {**t, "congestion_level": "LOW", "causes": []} for t in targets
                ]
            }
        )

    token = request_id.set(existing_id)
    try:
        analyzer = LLMCongestionAnalyzer(
            score=score, policy=RelevancePolicy(0.2, 0.8), complete=complete
        )
        if mode == "failure":
            with pytest.raises(RuntimeError, match="private details"):
                analyze_message(message, analyzer)
        else:
            result = analyze_message(message, analyzer)
            assert result.analysis_status == "SUCCESS"
            if mode in {"no_candidates", "all_unrelated"}:
                assert result.result.congestion_level == "UNKNOWN"
                assert result.result.report_count == 0
        assert request_id.get() == existing_id
    finally:
        request_id.reset(token)
    logs = [
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("app.features.congestion")
    ]
    assert logs[0].startswith("congestion analysis started: ")
    assert re.search(r"bundles=\d+ posts=\d+ comments=\d+ context_only=\d+", logs[0])
    for log in logs:
        assert log.endswith(f" request_id={existing_id or message.event_id}")
        assert all("=" in part for part in log.split(": ", 1)[1].split())
        assert "author_key" not in log and "private" not in log
    if mode == "failure":
        assert "code=INTERNAL" in logs[-1]
    else:
        assert logs[-1].startswith("congestion analysis done:")
        assert any(log.startswith("congestion aggregate done:") for log in logs)
        if mode in {"no_candidates", "all_unrelated"}:
            assert any(f"reason={mode}" in log for log in logs)
            assert not any(log.startswith("congestion judge done:") for log in logs)
        else:
            assert any(log.startswith("congestion relevance done:") for log in logs)
            assert any(log.startswith("congestion judge done:") for log in logs)
            assert any("decision=UNIQUE_TOP" in log for log in logs)
