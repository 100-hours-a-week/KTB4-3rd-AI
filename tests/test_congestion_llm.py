"""실제 분석기의 연결과 모델 출력 검증. 네트워크 호출은 모의 함수로 대체한다."""

import json
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import ModelNotReady, UpstreamError
from app.features.congestion import extractor
from app.features.congestion.extractor import ModelOutputInvalid, assess_bundle
from app.features.congestion.models import CongestionAnalysisRequest
from app.features.congestion.preprocessor import PreparedSpot
from app.features.congestion.relevance import RelevancePolicy
from app.features.congestion.service import LLMCongestionAnalyzer, get_analyzer
from app.main import app

AS_OF = "2026-10-08T12:00:00+09:00"


def test_congestion_uses_dedicated_model_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    def complete(name: str, *, prompt: str) -> str:
        if name != "api_2":
            raise ValueError("wrong model slot")
        return '{"signals": []}'

    monkeypatch.setattr(extractor, "llm", complete)
    assert extractor.call_api_model("prompt") == '{"signals": []}'


def request_data() -> dict[str, Any]:
    return {
        "request_id": "llm-1",
        "as_of": AS_OF,
        "window_minutes": 10,
        "spots": [
            {
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
        ],
    }


def decision(signals: list[dict[str, Any]]) -> str:
    return json.dumps({"signals": signals})


def test_old_parent_and_comment_are_context_only() -> None:
    request = CongestionAnalysisRequest.model_validate(request_data())
    prompts: list[dict[str, Any]] = []

    def complete(prompt: str) -> str:
        prompts.append(json.loads(prompt.split("입력 JSON:\n", 1)[1]))
        return decision(
            [
                {
                    "signal_type": "comment",
                    "signal_id": 20,
                    "congestion_level": "MEDIUM",
                    "causes": ["EVENT"],
                }
            ]
        )

    signals = assess_bundle(request.spots[0].post_bundles[0], request.as_of, complete)
    assert len(prompts) == 1
    assert prompts[0]["post"]["post_id"] == 10
    assert [comment["comment_id"] for comment in prompts[0]["comments"]] == [20, 21]
    assert prompts[0]["targets"] == [{"signal_type": "comment", "signal_id": 20}]
    assert len(signals) == 1
    assert signals[0].author_key == "current-author"
    assert signals[0].created_at == datetime.fromisoformat("2026-10-08T11:55:00+09:00")
    assert signals[0].congestion_level == "MEDIUM"
    assert signals[0].causes == frozenset({"EVENT"})


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        decision([]),
        decision(
            [
                {
                    "signal_type": "post",
                    "signal_id": 10,
                    "congestion_level": "HIGH",
                    "causes": [],
                }
            ]
        ),
        decision(
            [
                {
                    "signal_type": "comment",
                    "signal_id": 20,
                    "congestion_level": "UNKNOWN",
                    "causes": [],
                }
            ]
        ),
        decision(
            [
                {
                    "signal_type": "comment",
                    "signal_id": 20,
                    "congestion_level": None,
                    "causes": ["UNKNOWN"],
                }
            ]
        ),
        decision(
            [
                {
                    "signal_type": "comment",
                    "signal_id": 20,
                    "congestion_level": None,
                    "causes": ["EVENT", "EVENT"],
                }
            ]
        ),
        decision(
            [
                {
                    "signal_type": "comment",
                    "signal_id": 20,
                    "congestion_level": None,
                    "causes": [],
                    "unexpected": True,
                }
            ]
        ),
    ],
)
def test_invalid_model_output_is_rejected(raw: str) -> None:
    request = CongestionAnalysisRequest.model_validate(request_data())
    with pytest.raises(ModelOutputInvalid):
        assess_bundle(request.spots[0].post_bundles[0], request.as_of, lambda _: raw)


def test_current_post_and_comment_are_both_required() -> None:
    data = request_data()
    data["spots"][0]["post_bundles"][0]["created_at"] = "2026-10-08T11:50:00+09:00"
    request = CongestionAnalysisRequest.model_validate(data)
    raw = decision(
        [
            {
                "signal_type": "comment",
                "signal_id": 20,
                "congestion_level": None,
                "causes": [],
            }
        ]
    )
    with pytest.raises(ModelOutputInvalid):
        assess_bundle(request.spots[0].post_bundles[0], request.as_of, lambda _: raw)


def configured_analyzer(complete: Callable[[str], str]) -> LLMCongestionAnalyzer:
    def related(prepared: PreparedSpot) -> dict[tuple[str, int], float]:
        return {(target.signal_type, target.signal_id): 0.9 for target in prepared.candidates}

    return LLMCongestionAnalyzer(score=related, policy=RelevancePolicy(0.2, 0.8), complete=complete)


def test_llm_analyzer_groups_selected_bundles_into_one_call() -> None:
    data = request_data()
    spot = data["spots"][0]
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
    request = CongestionAnalysisRequest.model_validate(data)
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

    result = configured_analyzer(complete).analyze(
        request.spots[0], request.as_of, timedelta(minutes=request.window_minutes)
    )
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


def test_http_uses_real_analyzer_pipeline_with_injected_model(use_mock_llm: list[str]) -> None:
    data = request_data()
    data["spots"].append({"spot_id": 2, "post_bundles": []})
    response = TestClient(app).post("/congestion-analyses", json=data)
    assert response.status_code == 200
    assert len(use_mock_llm) == 1
    assert response.json()["spots"] == [
        {
            "spot_id": 1,
            "analysis_status": "SUCCESS",
            "result": {
                "congestion_level": "MEDIUM",
                "report_count": 1,
                "causes": [{"cause_code": "EVENT", "report_count": 1}],
                "summary": "제보 기준 혼잡도는 보통이며, 혼잡 제보자는 1명입니다. "
                "제보된 원인은 행사·공연·경기(1명)입니다.",
            },
        },
        {
            "spot_id": 2,
            "analysis_status": "SUCCESS",
            "result": {
                "congestion_level": "UNKNOWN",
                "report_count": 0,
                "causes": [],
                "summary": "혼잡 단계를 판단할 유효 제보가 없습니다.",
            },
        },
    ]


@pytest.mark.parametrize("error", [ModelNotReady(), UpstreamError(), ModelOutputInvalid("bad")])
def test_expected_model_error_fails_only_its_spot(error: Exception) -> None:
    data = request_data()
    data["spots"].append({"spot_id": 2, "post_bundles": []})

    def fail(_prompt: str) -> str:
        raise error

    app.dependency_overrides[get_analyzer] = lambda: configured_analyzer(fail)
    try:
        response = TestClient(app).post("/congestion-analyses", json=data)
    finally:
        app.dependency_overrides.pop(get_analyzer, None)
    assert response.status_code == 200
    assert response.json()["spots"] == [
        {
            "spot_id": 1,
            "analysis_status": "FAILED",
            "result": None,
            "code": "ANALYSIS_FAILED",
            "message": "analysis failed",
        },
        {
            "spot_id": 2,
            "analysis_status": "SUCCESS",
            "result": {
                "congestion_level": "UNKNOWN",
                "report_count": 0,
                "causes": [],
                "summary": "혼잡 단계를 판단할 유효 제보가 없습니다.",
            },
        },
    ]


def test_failed_spot_does_not_retry_but_next_spot_continues() -> None:
    data = request_data()
    first_spot = data["spots"][0]
    first_spot["post_bundles"].append(
        dict(
            first_spot["post_bundles"][0],
            post_id=11,
            created_at="2026-10-08T11:59:00+09:00",
            comments=[],
        )
    )
    second_bundle = dict(
        first_spot["post_bundles"][0],
        post_id=12,
        comments=[dict(first_spot["post_bundles"][0]["comments"][0], comment_id=22)],
    )
    data["spots"].append({"spot_id": 2, "post_bundles": [second_bundle]})
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
                    "congestion_level": "MEDIUM" if target["signal_id"] == 22 else None,
                    "causes": [],
                }
                for target in payload["targets"]
            ]
        )

    app.dependency_overrides[get_analyzer] = lambda: configured_analyzer(complete)
    try:
        response = TestClient(app).post("/congestion-analyses", json=data)
    finally:
        app.dependency_overrides.pop(get_analyzer, None)
    assert response.status_code == 200
    assert sorted(called) == [1, 2]
    assert response.json()["spots"][0]["analysis_status"] == "FAILED"
    assert response.json()["spots"][1]["result"] == {
        "congestion_level": "MEDIUM",
        "report_count": 1,
        "causes": [{"cause_code": "UNKNOWN", "report_count": 0}],
        "summary": "제보 기준 혼잡도는 보통이며, 혼잡 제보자는 1명입니다. "
        "구체적인 혼잡 원인은 제보되지 않았습니다.",
    }
