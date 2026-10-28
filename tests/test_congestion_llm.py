"""실제 분석기의 연결과 모델 출력 검증. 네트워크 호출은 모의 함수로 대체한다."""

import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import ModelNotReady, UpstreamError
from app.features.congestion import extractor
from app.features.congestion.extractor import ModelOutputInvalid
from app.features.congestion.models import CongestionAnalysisMessage
from app.features.congestion.preprocessor import PreparedSpot
from app.features.congestion.relevance import RelevancePolicy
from app.features.congestion.service import LLMCongestionAnalyzer, get_analyzer
from app.main import app


def test_congestion_uses_model_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def complete(prompt: str) -> str:
        assert prompt == "prompt"
        return '{"signals": []}'

    monkeypatch.setattr(extractor.model_client, "complete_json", complete)
    assert extractor.call_api_model("prompt") == '{"signals": []}'


def request_data() -> dict[str, Any]:
    return {
        "event_id": "llm-1",
        "schema_version": 1,
        "window_end": "2026-10-08T12:00:00+09:00",
        "spot_id": 1,
        "post_bundles": [
            {
                "post_id": 10,
                "context_only": False,
                "title": "오래된 원글",
                "content": "어제는 한산했어요.",
                "author_key": "old-author",
                "created_at": "2026-10-08T11:00:00+09:00",
                "comments": [
                    {
                        "comment_id": 20,
                        "content": "지금 사람이 많아요. 행사 때문이에요.",
                        "author_key": "current-author",
                        "created_at": "2026-10-08T11:55:00+09:00",
                    },
                    {
                        "comment_id": 21,
                        "content": "아까는 한산했어요.",
                        "author_key": "old-commenter",
                        "created_at": "2026-10-08T11:10:00+09:00",
                    },
                ],
            }
        ],
    }


def decision(signals: list[dict[str, Any]]) -> str:
    return json.dumps({"signals": signals})


def configured_analyzer(complete: Callable[[str], str]) -> LLMCongestionAnalyzer:
    def related(prepared: PreparedSpot) -> dict[tuple[str, int], float]:
        return {(target.signal_type, target.signal_id): 0.9 for target in prepared.candidates}

    return LLMCongestionAnalyzer(score=related, policy=RelevancePolicy(0.2, 0.8), complete=complete)


def test_llm_analyzer_groups_selected_bundles_into_one_call() -> None:
    data = request_data()
    spot = data
    old = dict(spot["post_bundles"][0], post_id=11, comments=[])
    current = dict(
        spot["post_bundles"][0],
        post_id=12,
        title="현재 글",
        content="지금 사람이 엄청 많아요.",
        author_key="current-author",
        created_at="2026-10-08T11:56:00+09:00",
        comments=[],
    )
    spot["post_bundles"].extend([old, current])
    request = CongestionAnalysisMessage.model_validate(data)
    called: list[dict[str, Any]] = []

    def complete(prompt: str) -> str:
        payload = json.loads(prompt.split("입력 JSON:\n", 1)[1])
        called.append(payload)
        return decision(
            [
                {
                    "signal_type": target["signal_type"],
                    "signal_id": target["signal_id"],
                    "congestion_level": "HIGH" if target["signal_id"] == 12 else "MEDIUM",
                    "causes": ["EVENT"],
                }
                for target in payload["targets"]
            ]
        )

    result = configured_analyzer(complete).analyze(request)
    assert len(called) == 1
    assert [bundle["post_id"] for bundle in called[0]["post_bundles"]] == [10, 11, 12]
    assert called[0]["targets"] == [
        {"signal_type": "post", "signal_id": 10},
        {"signal_type": "comment", "signal_id": 20},
        {"signal_type": "comment", "signal_id": 21},
        {"signal_type": "post", "signal_id": 11},
        {"signal_type": "post", "signal_id": 12},
    ]
    assert result.congestion_level == "MEDIUM"
    assert result.report_count == 3
    assert [(cause.cause_code, cause.report_count) for cause in result.causes] == [("EVENT", 3)]


@pytest.fixture
def use_mock_llm() -> Iterator[list[str]]:
    calls: list[str] = []

    def complete(prompt: str) -> str:
        calls.append(prompt)
        payload = json.loads(prompt.split("입력 JSON:\n", 1)[1])
        return decision(
            [
                {
                    **target,
                    "congestion_level": "MEDIUM" if target["signal_id"] == 20 else None,
                    "causes": ["EVENT"] if target["signal_id"] == 20 else [],
                }
                for target in payload["targets"]
            ]
        )

    app.dependency_overrides[get_analyzer] = lambda: configured_analyzer(complete)
    yield calls
    app.dependency_overrides.pop(get_analyzer, None)


def test_http_uses_real_analyzer_pipeline_with_injected_model(
    use_mock_llm: list[str],
) -> None:
    data = request_data()
    response = TestClient(app).post("/congestion-analyses", json=data)
    assert response.status_code == 200
    assert len(use_mock_llm) == 1
    assert {
        key: value
        for key, value in response.json().items()
        if key not in {"event_id", "schema_version", "window_end", "analyzed_at"}
    } == {
        "spot_id": 1,
        "analysis_status": "SUCCESS",
        "result": {
            "congestion_level": "MEDIUM",
            "report_count": 1,
            "causes": [{"cause_code": "EVENT", "report_count": 1}],
            "summary": "제보 기준 혼잡도는 보통이며, 혼잡 제보자는 1명입니다. 제보된 원인은 행사·공연·경기(1명)입니다.",
        },
    }


@pytest.mark.parametrize(
    "error", [ModelNotReady(), UpstreamError(), ModelOutputInvalid("bad")]
)
def test_expected_model_error_returns_http_error(error: Exception) -> None:
    data = request_data()

    def fail(_prompt: str) -> str:
        raise error

    app.dependency_overrides[get_analyzer] = lambda: configured_analyzer(fail)
    try:
        response = TestClient(app).post("/congestion-analyses", json=data)
    finally:
        app.dependency_overrides.pop(get_analyzer, None)
    assert response.status_code == (503 if isinstance(error, ModelNotReady) else 502)
    assert response.json()["code"] == (
        "MODEL_NOT_READY"
        if isinstance(error, ModelNotReady)
        else "CONGESTION_PARSE_FAILED"
        if isinstance(error, ModelOutputInvalid)
        else "UPSTREAM_FAILED"
    )


def test_failed_message_does_not_retry_and_next_request_continues() -> None:
    data = request_data()
    called: list[int] = []

    def complete(prompt: str) -> str:
        payload = json.loads(prompt.split("입력 JSON:\n", 1)[1])
        called.append(payload["spot_id"])
        if payload["spot_id"] == 1:
            raise UpstreamError()
        return decision(
            [
                {
                    **target,
                    "congestion_level": "MEDIUM" if target["signal_id"] == 20 else None,
                    "causes": [],
                }
                for target in payload["targets"]
            ]
        )

    app.dependency_overrides[get_analyzer] = lambda: configured_analyzer(complete)
    try:
        client = TestClient(app)
        failed = client.post("/congestion-analyses", json=data)
        data["spot_id"] = 2
        successful = client.post("/congestion-analyses", json=data)
    finally:
        app.dependency_overrides.pop(get_analyzer, None)
    assert failed.status_code == 502
    assert successful.status_code == 200
    assert called == [1, 2]
    assert failed.json()["code"] == "UPSTREAM_FAILED"
    assert successful.json()["result"] == {
        "congestion_level": "MEDIUM",
        "report_count": 1,
        "causes": [{"cause_code": "UNKNOWN", "report_count": 0}],
        "summary": "제보 기준 혼잡도는 보통이며, 혼잡 제보자는 1명입니다. "
        "구체적인 혼잡 원인은 제보되지 않았습니다.",
    }
