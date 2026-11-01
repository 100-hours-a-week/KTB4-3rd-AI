"""Decisions HTTP 계약과 응답 검증을 모의 전송 계층에서 확인한다."""

import json
from datetime import datetime
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
                    "context_only": True,
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
                    "context_only": False,
                    "title": "일반 글",
                    "content": "점심 뭐 먹을까요?",
                    "author_key": "third",
                    "created_at": "2026-10-12T11:57:00+09:00",
                    "comments": [],
                },
            ],
        }
    )
    return prepare_spot(spot, datetime.fromisoformat("2026-10-12T12:00:00+09:00"))


def test_spot_request_classifies_all_candidates_and_keeps_parent_context(
    prepared: PreparedSpot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY_2", "test-key")
    received: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.openai.com/v1/decisions"
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        received.append(payload)
        scores = {"comment_1": 0.9, "comment_2": 0.0, "comment_3": 0.5, "post_3": 0.01}
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

    scorer = DecisionsRelevanceScorer(transport=httpx.MockTransport(respond))
    result = classify_spot(
        prepared, scorer, RelevancePolicy(exclude_below=0.2, related_at_or_above=0.8)
    )
    assert [item.probability for item in result.decisions] == [0.9, 0.0, 0.5, 0.01]
    assert [(item.signal_type, item.signal_id) for item in result.selected_candidates] == [
        ("comment", 1),
        ("comment", 3),
    ]
    assert [[item["name"] for item in payload["questions"]] for payload in received] == [
        ["comment_1", "comment_2", "comment_3", "post_3"],
    ]
    assert all(payload["model"] == "gpt-6-luna" for payload in received)
    context = json.loads(received[0]["input"])
    assert len(context["post_bundles"]) == 2
    assert context["window_end"] == "2026-10-12T12:00:00+09:00"
    assert context["post_bundles"][0]["title"] == "오래된 부모"
    assert [item["comment_id"] for item in context["post_bundles"][0]["comments"]] == [1, 2, 3]


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
    ],
)
def test_invalid_answers_cannot_produce_partial_scores(
    prepared: PreparedSpot, monkeypatch: pytest.MonkeyPatch, body: Any
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY_2", "test-key")
    scorer = DecisionsRelevanceScorer(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    )
    with pytest.raises(RelevanceOutputInvalid):
        scorer(prepared)


@pytest.mark.parametrize("probability", [-0.1, 1.1, True, "0.9", None])
def test_invalid_probability_is_rejected(
    prepared: PreparedSpot, monkeypatch: pytest.MonkeyPatch, probability: Any
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY_2", "test-key")
    body = {
        "answers": [
            {"type": "predicate", "name": "comment_1", "probability": probability},
            {"type": "predicate", "name": "comment_3", "probability": 0.5},
        ]
    }
    scorer = DecisionsRelevanceScorer(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    )
    with pytest.raises(RelevanceOutputInvalid):
        scorer(prepared)


def test_non_json_response_is_rejected(
    prepared: PreparedSpot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY_2", "test-key")
    scorer = DecisionsRelevanceScorer(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="not json"))
    )
    with pytest.raises(UpstreamError):
        scorer(prepared)


@pytest.mark.parametrize("status", [401, 429, 500])
def test_http_errors_are_reported_as_provider_errors(
    prepared: PreparedSpot, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY_2", "test-key")
    scorer = DecisionsRelevanceScorer(
        transport=httpx.MockTransport(lambda _: httpx.Response(status))
    )
    with pytest.raises(UpstreamError):
        scorer(prepared)


def test_timeout_after_a_successful_batch_returns_no_partial_scores(
    prepared: PreparedSpot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY_2", "test-key")
    monkeypatch.setattr("app.features.congestion.decisions.DECISIONS_MAX_QUESTIONS", 3)

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
        transport=httpx.MockTransport(first_success_then_timeout)
    )
    with pytest.raises(UpstreamError):
        scorer(prepared)


def test_missing_key_prevents_network_call(
    prepared: PreparedSpot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY_2", raising=False)

    def unexpected(_request: httpx.Request) -> httpx.Response:
        pytest.fail("키 없이 네트워크가 호출됨")

    scorer = DecisionsRelevanceScorer(transport=httpx.MockTransport(unexpected))
    with pytest.raises(ModelNotReady):
        scorer(prepared)


def test_empty_spot_needs_no_key_or_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY_2", raising=False)
    prepared = prepare_spot(
        CongestionSpotInput(spot_id=1, post_bundles=[]),
        datetime.fromisoformat("2026-10-12T12:00:00+09:00"),
    )

    def unexpected(_request: httpx.Request) -> httpx.Response:
        pytest.fail("빈 후보에 대한 네트워크가 호출됨")

    assert (
        DecisionsRelevanceScorer(transport=httpx.MockTransport(unexpected))(prepared)
        == {}
    )


@pytest.mark.parametrize("candidate_count, sizes", [(400, [400]), (401, [400, 1]), (801, [400, 400, 1])])
def test_questions_are_split_with_identical_spot_input(
    candidate_count: int, sizes: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY_2", "test-key")
    posts = []
    remaining = candidate_count
    while remaining:
        count = min(remaining, 101)
        post_id = len(posts) + 1
        posts.append({
            "post_id": post_id, "context_only": False, "title": "합성 원글",
            "content": "지금 승강장이 한산해요.", "author_key": f"synthetic-post-{post_id}",
            "created_at": "2026-11-02T12:00:00+09:00",
            "comments": [
                {"comment_id": (post_id - 1) * 100 + i + 1, "content": "합성 댓글",
                 "author_key": f"synthetic-comment-{post_id}-{i}",
                 "created_at": "2026-11-02T12:00:00+09:00"}
                for i in range(count - 1)
            ],
        })
        remaining -= count
    spot = CongestionSpotInput.model_validate({"spot_id": 7, "post_bundles": posts})
    prepared = prepare_spot(spot, datetime.fromisoformat("2026-11-02T12:00:00+09:00"))
    received = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        received.append(body)
        return httpx.Response(200, json={"answers": [
            {"type": "predicate", "name": q["name"], "probability": 0.9}
            for q in reversed(body["questions"])
        ]})

    result = DecisionsRelevanceScorer(transport=httpx.MockTransport(respond))(prepared)
    assert len(result) == candidate_count
    assert set(result) == {(c.signal_type, c.signal_id) for c in prepared.candidates}
    assert [len(body["questions"]) for body in received] == sizes
    assert all(body["input"] == received[0]["input"] for body in received)
    assert json.loads(received[0]["input"])["post_bundles"] == posts


def test_refusal_keeps_only_that_candidate_uncertain_and_logs_count(
    prepared: PreparedSpot, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.features.congestion.models import CongestionAnalysisMessage
    from app.features.congestion.service import LLMCongestionAnalyzer

    monkeypatch.setenv("OPENAI_API_KEY_2", "test-key")

    def respond(request: httpx.Request) -> httpx.Response:
        questions = json.loads(request.content)["questions"]
        return httpx.Response(200, json={"answers": [
            {"type": "refusal", "name": q["name"]} if q["name"] == "comment_1" else
            {"type": "predicate", "name": q["name"], "probability": 0.0}
            for q in reversed(questions)
        ]})

    scorer = DecisionsRelevanceScorer(transport=httpx.MockTransport(respond))
    policy = RelevancePolicy(0.2, 0.8)
    classified = classify_spot(prepared, scorer, policy)
    assert [d.status for d in classified.decisions] == [
        "UNCERTAIN", "UNRELATED", "UNRELATED", "UNRELATED"
    ]
    assert classified.decisions[0].probability is None
    assert classified.selected_candidates == (prepared.candidates[0],)

    def complete(prompt: str) -> str:
        targets = json.loads(prompt.split("입력 JSON:\n", 1)[1])["targets"]
        assert targets == [{"signal_type": "comment", "signal_id": 1}]
        return json.dumps({"signals": [
            {**t, "congestion_level": "LOW", "causes": []} for t in targets
        ]})

    message = CongestionAnalysisMessage.model_validate({
        "event_id": "synthetic-refusal", "schema_version": 1, "spot_id": prepared.spot_id,
        "window_end": prepared.window_end.isoformat(),
        "post_bundles": [p.model_dump(mode="json") for p in prepared.post_bundles],
    })
    result = LLMCongestionAnalyzer(score=scorer, policy=policy, complete=complete).analyze(message)
    assert result.congestion_level == "LOW"
    assert any("uncertain=1 unrelated=3 refused=1 ms=" in r.getMessage() for r in caplog.records)
