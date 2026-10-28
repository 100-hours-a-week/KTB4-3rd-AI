from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.features.congestion.models import (
    CongestionAnalysisMessage,
    CongestionResult,
)
from app.features.congestion.service import (
    FakeCongestionAnalyzer,
    analyze_message,
    failure_result,
    get_analyzer,
)
from app.main import app

ENDPOINT = "/congestion-analyses"
INVALID = {"success": False, "code": "REQUEST_INVALID", "message": "request is invalid"}


@pytest.fixture(autouse=True)
def use_fake_analyzer_for_existing_contract_tests() -> Iterator[None]:
    previous = app.dependency_overrides.get(get_analyzer)
    app.dependency_overrides[get_analyzer] = FakeCongestionAnalyzer
    yield
    if previous is None:
        app.dependency_overrides.pop(get_analyzer, None)
    else:
        app.dependency_overrides[get_analyzer] = previous


@pytest.fixture
def payload() -> dict[str, Any]:
    return {
        "event_id": "req-001",
        "schema_version": 1,
        "window_end": "2026-10-07T15:00:00+09:00",
        "spot_id": 1,
        "post_bundles": [
            {
                "post_id": 10,
                "context_only": False,
                "title": "현재 상황",
                "content": "사람이 많아요.",
                "author_key": "author-1",
                "created_at": "2026-10-07T14:55:00+09:00",
                "comments": [
                    {
                        "comment_id": 10,
                        "content": "저도 여기 있어요.",
                        "author_key": "author-1",
                        "created_at": "2026-10-07T14:56:00+09:00",
                    }
                ],
            }
        ],
    }


def node(payload: dict[str, Any], level: str) -> dict[str, Any]:
    if level == "request":
        return payload
    post = payload["post_bundles"][0]
    return post if level == "post" else post["comments"][0]


def assert_invalid(payload: object) -> None:
    response = TestClient(app).post(ENDPOINT, json=payload)
    assert response.status_code == 422
    assert response.json() == INVALID


@pytest.mark.parametrize("failed", [False, True])
def test_message_result_preserves_window_and_has_completion_metadata(
    payload: dict[str, Any], failed: bool
) -> None:
    before = datetime.now(UTC)
    if failed:
        body = failure_result(
            CongestionAnalysisMessage.model_validate(payload)
        ).model_dump(mode="json")
    else:
        response = TestClient(app).post(ENDPOINT, json=payload)
        assert response.status_code == 200
        body = response.json()
    after = datetime.now(UTC)
    assert body["spot_id"] == payload["spot_id"]
    assert body["window_end"] == payload["window_end"]
    returned = datetime.fromisoformat(body["window_end"])
    requested = datetime.fromisoformat(payload["window_end"])
    assert returned == requested
    assert returned.utcoffset() == requested.utcoffset()
    analyzed_at = datetime.fromisoformat(body["analyzed_at"])
    assert analyzed_at.tzinfo is not None
    assert before <= analyzed_at <= after
    assert UUID(body["event_id"]).version == 4
    assert body["event_id"] != payload["event_id"]
    assert body["schema_version"] == 1
    assert body["analysis_status"] == ("FAILED" if failed else "SUCCESS")
    if failed:
        assert body["result"] is None
        assert body["code"] == "ANALYSIS_FAILED"
        assert body["message"] == "analysis failed"
    else:
        assert body["result"] == {
            "congestion_level": "MEDIUM",
            "report_count": 1,
            "causes": [{"cause_code": "EVENT", "report_count": 1}],
            "summary": "제보 기준 혼잡도는 보통이며, 혼잡 제보자는 1명입니다. "
            "제보된 원인은 행사·공연·경기(1명)입니다.",
        }
    assert "spots" not in body
    assert "request_id" not in body


FIELDS = [
    ("request", "event_id"),
    ("request", "window_end"),
    ("request", "schema_version"),
    ("request", "spot_id"),
    ("request", "post_bundles"),
    ("post", "context_only"),
    ("post", "post_id"),
    ("post", "title"),
    ("post", "content"),
    ("post", "author_key"),
    ("post", "created_at"),
    ("post", "comments"),
    ("comment", "comment_id"),
    ("comment", "content"),
    ("comment", "author_key"),
    ("comment", "created_at"),
]


@pytest.mark.parametrize(("level", "field"), FIELDS)
@pytest.mark.parametrize("mode", ["missing", "null"])
def test_required_fields(payload: dict[str, Any], level: str, field: str, mode: str) -> None:
    target = node(payload, level)
    if mode == "missing":
        del target[field]
    else:
        target[field] = None
    assert_invalid(payload)


@pytest.mark.parametrize("level", ["request", "post", "comment"])
def test_unknown_fields_rejected(payload: dict[str, Any], level: str) -> None:
    node(payload, level)["unexpected"] = True
    assert_invalid(payload)


@pytest.mark.parametrize(
    ("level", "field"),
    [
        ("request", "spot_id"),
        ("post", "post_id"),
        ("comment", "comment_id"),
    ],
)
@pytest.mark.parametrize("value", [0, -1, "1", 1.0, True])
def test_ids_are_strict_positive_integers(
    payload: dict[str, Any], level: str, field: str, value: object
) -> None:
    node(payload, level)[field] = value
    assert_invalid(payload)


@pytest.mark.parametrize("duplicate", ["post", "comment_in_post", "comment_across_posts"])
def test_duplicate_ids_rejected_in_message(payload: dict[str, Any], duplicate: str) -> None:
    post = node(payload, "post")
    if duplicate == "post":
        payload["post_bundles"].append(dict(post, comments=[]))
    elif duplicate == "comment_in_post":
        post["comments"].append(deepcopy(post["comments"][0]))
    else:
        payload["post_bundles"].append(dict(post, post_id=11))
    assert_invalid(payload)


@pytest.mark.parametrize(
    ("level", "field"),
    [
        ("request", "window_end"),
        ("post", "created_at"),
        ("comment", "created_at"),
    ],
)
@pytest.mark.parametrize(
    "value", ["2026-10-07T15:00:00", "2026-10-07", 1791352800, "1791352800", "invalid"]
)
def test_timestamps_require_iso_string_and_timezone(
    payload: dict[str, Any], level: str, field: str, value: object
) -> None:
    node(payload, level)[field] = value
    assert_invalid(payload)


TEXT_FIELDS = [
    ("request", "event_id", 128),
    ("post", "author_key", 128),
    ("comment", "author_key", 128),
    ("post", "title", 30),
    ("post", "content", 280),
    ("comment", "content", 280),
]


@pytest.mark.parametrize(("level", "field", "limit"), TEXT_FIELDS)
@pytest.mark.parametrize("value", ["", " \n\t", 123])
def test_text_requires_nonblank_string(
    payload: dict[str, Any], level: str, field: str, limit: int, value: object
) -> None:
    node(payload, level)[field] = value
    assert_invalid(payload)


@pytest.mark.parametrize(("level", "field", "limit"), TEXT_FIELDS)
def test_text_length_boundaries(
    payload: dict[str, Any], level: str, field: str, limit: int
) -> None:
    target = node(payload, level)
    target[field] = "가" * limit
    assert TestClient(app).post(ENDPOINT, json=payload).status_code == 200
    target[field] += "가"
    assert_invalid(payload)


def test_empty_post_bundles_rejected_before_analysis(payload: dict[str, Any]) -> None:
    class UnexpectedAnalyzer:
        def analyze(self, message: CongestionAnalysisMessage) -> CongestionResult:
            pytest.fail("빈 묶음이 분석기에 전달됨")

    app.dependency_overrides[get_analyzer] = UnexpectedAnalyzer
    payload["post_bundles"] = []
    assert_invalid(payload)


def test_skeleton_still_does_not_filter_old_content_or_judge_text(
    payload: dict[str, Any],
) -> None:
    node(payload, "post")["created_at"] = "2000-01-01T00:00:00Z"
    node(payload, "post")["content"] = "혼잡도와 무관한 글"
    node(payload, "comment")["created_at"] = "2000-01-01T00:00:00+00:00"
    response = TestClient(app).post(ENDPOINT, json=payload)
    assert response.status_code == 200
    assert response.json()["result"]["congestion_level"] == "MEDIUM"


@pytest.mark.parametrize("level", ["post", "comment"])
def test_future_signal_is_accepted(payload: dict[str, Any], level: str) -> None:
    node(payload, level)["created_at"] = "2026-10-07T06:00:01Z"
    assert TestClient(app).post(ENDPOINT, json=payload).status_code == 200


@pytest.mark.parametrize("kind", ["posts", "comments", "total_signals"])
def test_request_size_boundaries(payload: dict[str, Any], kind: str) -> None:
    post = node(payload, "post")
    comment = deepcopy(node(payload, "comment"))
    if kind == "posts":
        target = payload["post_bundles"]
        target[:] = [dict(post, post_id=i + 1, comments=[]) for i in range(50)]
        overflow = dict(post, post_id=51, comments=[])
    elif kind == "comments":
        target = post["comments"]
        target[:] = [dict(comment, comment_id=i + 1) for i in range(100)]
        overflow = dict(comment, comment_id=101)
    else:
        # 원글 10개와 댓글 990개로 제보 1,000개를 만들되 각 목록의 개별 상한은 지킨다.
        target_posts = payload["post_bundles"]
        target_posts[:] = [
            dict(
                post,
                post_id=i + 1,
                comments=[dict(comment, comment_id=i * 99 + j + 1) for j in range(99)],
            )
            for i in range(10)
        ]
        target = target_posts[-1]["comments"]
        overflow = dict(comment, comment_id=991)
    assert TestClient(app).post(ENDPOINT, json=payload).status_code == 200
    target.append(overflow)
    assert_invalid(payload)


def test_empty_comments_and_stateless_requests(payload: dict[str, Any]) -> None:
    node(payload, "post")["comments"] = []
    client = TestClient(app)
    first = client.post(ENDPOINT, json=payload).json()
    payload["spot_id"] = 2
    second = client.post(ENDPOINT, json=payload).json()
    assert first["spot_id"] == 1
    assert second["spot_id"] == 2
    assert first["event_id"] != second["event_id"]
    assert first["result"] == second["result"]


def test_analyzer_receives_one_message_and_preserves_input(
    payload: dict[str, Any],
) -> None:
    calls: list[CongestionAnalysisMessage] = []

    class ReplacementAnalyzer:
        def analyze(self, message: CongestionAnalysisMessage) -> CongestionResult:
            calls.append(message)
            return CongestionResult(congestion_level="LOW", report_count=2, causes=[])

    node(payload, "post")["content"] = "  사람이 많아요.  "
    original = deepcopy(payload)
    app.dependency_overrides[get_analyzer] = ReplacementAnalyzer
    response = TestClient(app).post(ENDPOINT, json=payload)
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0].model_dump(mode="json") == original
    assert payload == original
    assert response.json()["result"]["congestion_level"] == "LOW"


def test_unexpected_error_is_not_converted_to_failed_result(
    payload: dict[str, Any],
) -> None:
    class UnexpectedAnalyzer:
        def analyze(self, message: CongestionAnalysisMessage) -> CongestionResult:
            raise RuntimeError("unexpected analyzer bug")

    message = CongestionAnalysisMessage.model_validate(payload)
    with pytest.raises(RuntimeError, match="unexpected analyzer bug"):
        analyze_message(message, UnexpectedAnalyzer())


def test_malformed_json_uses_common_request_error() -> None:
    response = TestClient(app).post(
        ENDPOINT, content="{", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json() == INVALID


@pytest.mark.parametrize("value", [0, 1, "false", "true"])
def test_context_only_requires_boolean(payload: dict[str, Any], value: object) -> None:
    node(payload, "post")["context_only"] = value
    assert_invalid(payload)


@pytest.mark.parametrize("value", [True, "1", 1.0, 0, 2])
def test_schema_version_requires_strict_integer_one(payload: dict[str, Any], value: object) -> None:
    payload["schema_version"] = value
    assert_invalid(payload)


@pytest.mark.parametrize("field", ["request_id", "as_of", "window_minutes", "spots"])
def test_removed_request_fields_are_rejected(payload: dict[str, Any], field: str) -> None:
    payload[field] = 1
    assert_invalid(payload)


def test_swagger_examples_match_message_and_result_models() -> None:
    from pydantic import TypeAdapter

    from app.features.congestion.models import CongestionAnalysisResult
    from app.features.congestion.schema_examples import (
        REQUEST_EXAMPLE,
        RESPONSE_EXAMPLE,
    )

    message = CongestionAnalysisMessage.model_validate(REQUEST_EXAMPLE)
    assert message.post_bundles
    result = TypeAdapter(CongestionAnalysisResult).validate_python(RESPONSE_EXAMPLE)
    assert result.spot_id == message.spot_id
    assert result.window_end == message.window_end
    schema = TestClient(app).get("/openapi.json").json()
    operation = schema["paths"][ENDPOINT]["post"]
    assert (
        operation["requestBody"]["content"]["application/json"]["examples"]["default"]["value"]
        == REQUEST_EXAMPLE
    )
    assert "discriminator" in operation["responses"]["200"]["content"]["application/json"]["schema"]
