"""Decisions HTTP 계약과 응답 검증을 모의 전송 계층에서 확인한다."""

import json
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest

from app.core.exceptions import ModelNotReady, UpstreamError
from app.features.congestion.decisions import DecisionsRelevanceScorer
from app.features.congestion.models import CongestionSpotInput
from app.features.congestion.preprocessor import PreparedSpot, prepare_spot
from app.features.congestion.relevance import (
    RelevanceOutputInvalid,
    RelevancePolicy,
    classify_spot,
)


@pytest.fixture
def prepared() -> PreparedSpot:
    spot = CongestionSpotInput.model_validate(
        {
            "spot_id": 7,
            "post_bundles": [
                {
                    "post_id": 1,
                    "title": "오래된 부모",
                    "content": "여기 줄이 길어요.",
                    "author_key": "parent",
                    "created_at": "2026-10-12T10:00:00+09:00",
                    "comments": [
                        {
                            "comment_id": 1,
                            "content": "저는 여기 있는데 지금 한산해요.",
                            "author_key": "first",
                            "created_at": "2026-10-12T11:55:00+09:00",
                        },
                        {
                            "comment_id": 2,
                            "content": "이전 댓글의 문맥",
                            "author_key": "old-comment",
                            "created_at": "2026-10-12T11:00:00+09:00",
                        },
                        {
                            "comment_id": 3,
                            "content": "지금 고장 때문에 기다리고 있어요.",
                            "author_key": "second",
                            "created_at": "2026-10-12T11:56:00+09:00",
                        },
                    ],
                },
                {
                    "post_id": 3,
                    "title": "일반 글",
                    "content": "점심 뭐 먹을까요?",
                    "author_key": "third",
                    "created_at": "2026-10-12T11:57:00+09:00",
                    "comments": [],
                },
            ],
        }
    )
    return prepare_spot(
        spot, datetime.fromisoformat("2026-10-12T12:00:00+09:00"), timedelta(minutes=10)
    )


def test_bundle_requests_classify_each_current_signal_and_keep_parent_context(
    prepared: PreparedSpot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    received: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.openai.com/v1/decisions"
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        received.append(payload)
        scores = {"comment_1": 0.9, "comment_3": 0.5, "post_3": 0.01}
        return httpx.Response(
            200,
            json={
                "model": "gpt-6-luna",
                "usage": {"input_tokens": 42, "total_tokens": 42},
                "answers": [
                    {"type": "predicate", "name": item["name"], "probability": scores[item["name"]]}
                    for item in reversed(payload["questions"])
                ],
            },
        )

    scorer = DecisionsRelevanceScorer(timeout=5, transport=httpx.MockTransport(respond))
    result = classify_spot(
        prepared, scorer, RelevancePolicy(exclude_below=0.2, related_at_or_above=0.8)
    )
    assert [item.probability for item in result.decisions] == [0.9, 0.5, 0.01]
    assert [(item.signal_type, item.signal_id) for item in result.selected_candidates] == [
        ("comment", 1),
        ("comment", 3),
    ]
    assert [[item["name"] for item in payload["questions"]] for payload in received] == [
        ["comment_1", "comment_3"],
        ["post_3"],
    ]
    assert all(payload["model"] == "gpt-6-luna" for payload in received)
    context = json.loads(received[0]["input"])
    assert context["as_of"] == "2026-10-12T12:00:00+09:00"
    assert context["post_bundle"]["title"] == "오래된 부모"
    assert [item["comment_id"] for item in context["post_bundle"]["comments"]] == [1, 2, 3]


@pytest.mark.parametrize(
    "body",
    [
        {"answers": []},
        {"answers": [{"type": "predicate", "name": "comment_1", "probability": 0.9}]},
        {
            "answers": [
                {"type": "predicate", "name": "comment_1", "probability": 0.9},
                {"type": "predicate", "name": "comment_1", "probability": 0.5},
            ]
        },
        {
            "answers": [
                {"type": "predicate", "name": "comment_1", "probability": 0.9},
                {"type": "predicate", "name": "comment_99", "probability": 0.5},
            ]
        },
        {"answers": [{"type": "refusal", "name": "comment_1"}]},
        {"answers": [{"type": "choice", "name": "comment_1", "choice": "related"}]},
        [],
    ],
)
def test_invalid_answers_cannot_produce_partial_scores(
    prepared: PreparedSpot, monkeypatch: pytest.MonkeyPatch, body: Any
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scorer = DecisionsRelevanceScorer(
        timeout=5, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    )
    with pytest.raises(RelevanceOutputInvalid):
        scorer(prepared)


@pytest.mark.parametrize("probability", [-0.1, 1.1, True, "0.9", None])
def test_invalid_probability_is_rejected(
    prepared: PreparedSpot, monkeypatch: pytest.MonkeyPatch, probability: Any
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    body = {
        "answers": [
            {"type": "predicate", "name": "comment_1", "probability": probability},
            {"type": "predicate", "name": "comment_3", "probability": 0.5},
        ]
    }
    scorer = DecisionsRelevanceScorer(
        timeout=5, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    )
    with pytest.raises(RelevanceOutputInvalid):
        scorer(prepared)


def test_non_json_response_is_rejected(
    prepared: PreparedSpot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scorer = DecisionsRelevanceScorer(
        timeout=5, transport=httpx.MockTransport(lambda _: httpx.Response(200, text="not json"))
    )
    with pytest.raises(RelevanceOutputInvalid):
        scorer(prepared)


@pytest.mark.parametrize("status", [401, 429, 500])
def test_http_errors_are_reported_as_provider_errors(
    prepared: PreparedSpot, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scorer = DecisionsRelevanceScorer(
        timeout=5, transport=httpx.MockTransport(lambda _: httpx.Response(status))
    )
    with pytest.raises(UpstreamError):
        scorer(prepared)


def test_timeout_after_a_successful_bundle_returns_no_partial_scores(
    prepared: PreparedSpot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def first_success_then_timeout(request: httpx.Request) -> httpx.Response:
        questions = json.loads(request.content)["questions"]
        if questions[0]["name"] == "post_3":
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(
            200,
            json={
                "answers": [
                    {"type": "predicate", "name": item["name"], "probability": 0.9}
                    for item in questions
                ]
            },
        )

    scorer = DecisionsRelevanceScorer(
        timeout=5, transport=httpx.MockTransport(first_success_then_timeout)
    )
    with pytest.raises(UpstreamError):
        scorer(prepared)


def test_missing_key_prevents_network_call(
    prepared: PreparedSpot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def unexpected(_request: httpx.Request) -> httpx.Response:
        pytest.fail("키 없이 네트워크가 호출됨")

    scorer = DecisionsRelevanceScorer(timeout=5, transport=httpx.MockTransport(unexpected))
    with pytest.raises(ModelNotReady):
        scorer(prepared)


def test_empty_spot_needs_no_key_or_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    prepared = prepare_spot(
        CongestionSpotInput(spot_id=1, post_bundles=[]),
        datetime.fromisoformat("2026-10-12T12:00:00+09:00"),
        timedelta(minutes=10),
    )

    def unexpected(_request: httpx.Request) -> httpx.Response:
        pytest.fail("빈 후보에 대한 네트워크가 호출됨")

    assert (
        DecisionsRelevanceScorer(timeout=5, transport=httpx.MockTransport(unexpected))(prepared)
        == {}
    )


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, 10**1000])
def test_invalid_timeout_is_rejected(timeout: float) -> None:
    with pytest.raises(ValueError):
        DecisionsRelevanceScorer(timeout=timeout)
