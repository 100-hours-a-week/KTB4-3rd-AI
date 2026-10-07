from collections.abc import Iterator
from copy import deepcopy
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.features.congestion.models import CongestionResult, CongestionSpotInput
from app.features.congestion.service import (
    AnalysisError,
    FakeCongestionAnalyzer,
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
        "request_id": "req-001",
        "as_of": "2026-10-07T15:00:00+09:00",
        "spots": [
            {
                "spot_id": 1,
                "post_bundles": [
                    {
                        "post_id": 10,
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
            },
            {"spot_id": 2, "post_bundles": []},
        ],
    }


def node(payload: dict[str, Any], level: str) -> dict[str, Any]:
    if level == "request":
        return payload
    spot = payload["spots"][0]
    if level == "spot":
        return spot
    post = spot["post_bundles"][0]
    return post if level == "post" else post["comments"][0]


def assert_invalid(payload: object) -> None:
    response = TestClient(app).post(ENDPOINT, json=payload)
    assert response.status_code == 422
    assert response.json() == INVALID


def test_valid_request_returns_fake_result_and_empty_spot(
    payload: dict[str, Any],
) -> None:
    response = TestClient(app).post(ENDPOINT, json=payload)
    assert response.status_code == 200
    assert response.json() == {
        "request_id": "req-001",
        "spots": [
            {
                "spot_id": 1,
                "analysis_status": "SUCCESS",
                "result": {
                    "congestion_level": "MEDIUM",
                    "report_count": 1,
                    "causes": [{"cause_code": "EVENT", "report_count": 1}],
                },
            },
            {
                "spot_id": 2,
                "analysis_status": "SUCCESS",
                "result": {
                    "congestion_level": "UNKNOWN",
                    "report_count": 0,
                    "causes": [],
                },
            },
        ],
    }


FIELDS = [
    ("request", "request_id"),
    ("request", "as_of"),
    ("request", "spots"),
    ("spot", "spot_id"),
    ("spot", "post_bundles"),
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
def test_required_fields(
    payload: dict[str, Any], level: str, field: str, mode: str
) -> None:
    target = node(payload, level)
    if mode == "missing":
        del target[field]
    else:
        target[field] = None
    assert_invalid(payload)


@pytest.mark.parametrize("level", ["request", "spot", "post", "comment"])
def test_unknown_fields_rejected(payload: dict[str, Any], level: str) -> None:
    node(payload, level)["unexpected"] = True
    assert_invalid(payload)


@pytest.mark.parametrize(
    ("level", "field"),
    [
        ("spot", "spot_id"),
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


@pytest.mark.parametrize(
    "duplicate",
    [
        "spot",
        "post_in_spot",
        "post_across_spots",
        "comment_in_post",
        "comment_across_posts",
    ],
)
def test_duplicate_ids_rejected_across_request(
    payload: dict[str, Any], duplicate: str
) -> None:
    spot = node(payload, "spot")
    post = node(payload, "post")
    if duplicate == "spot":
        payload["spots"][1]["spot_id"] = 1
    elif duplicate.startswith("post"):
        copied = deepcopy(post)
        copied["comments"] = []
        target = spot if duplicate == "post_in_spot" else payload["spots"][1]
        target["post_bundles"].append(copied)
    elif duplicate == "comment_in_post":
        post["comments"].append(deepcopy(post["comments"][0]))
    else:
        copied = deepcopy(post)
        copied["post_id"] = 11
        payload["spots"][1]["post_bundles"].append(copied)
    assert_invalid(payload)


@pytest.mark.parametrize(
    ("level", "field"),
    [
        ("request", "as_of"),
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
    ("request", "request_id", 128),
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


def test_empty_request_rejected(payload: dict[str, Any]) -> None:
    payload["spots"] = []
    assert_invalid(payload)


def test_skeleton_still_does_not_filter_old_content_or_judge_text(
    payload: dict[str, Any],
) -> None:
    node(payload, "post")["created_at"] = "2000-01-01T00:00:00Z"
    node(payload, "post")["content"] = "혼잡도와 무관한 글"
    node(payload, "comment")["created_at"] = "2000-01-01T00:00:00+00:00"
    response = TestClient(app).post(ENDPOINT, json=payload)
    assert response.status_code == 200
    assert response.json()["spots"][0]["result"]["congestion_level"] == "MEDIUM"


@pytest.mark.parametrize("level", ["post", "comment"])
def test_future_signal_rejected_relative_to_as_of(
    payload: dict[str, Any], level: str
) -> None:
    # 15:00+09:00 is 06:00Z; compare instants rather than timestamp text.
    node(payload, level)["created_at"] = "2026-10-07T06:00:01Z"
    assert_invalid(payload)


@pytest.mark.parametrize("kind", ["spots", "posts", "comments", "total_signals"])
def test_request_size_boundaries(payload: dict[str, Any], kind: str) -> None:
    post = node(payload, "post")
    comment = deepcopy(node(payload, "comment"))
    if kind == "spots":
        payload["spots"] = [{"spot_id": i + 1, "post_bundles": []} for i in range(20)]
        overflow = {"spot_id": 21, "post_bundles": []}
        target = payload["spots"]
    elif kind == "posts":
        target = node(payload, "spot")["post_bundles"]
        target[:] = [dict(post, post_id=i + 1, comments=[]) for i in range(50)]
        overflow = dict(post, post_id=51, comments=[])
    elif kind == "comments":
        target = post["comments"]
        target[:] = [dict(comment, comment_id=i + 1) for i in range(100)]
        overflow = dict(comment, comment_id=101)
    else:
        # 10 posts + 990 comments = 1,000 signals; each list is within its own limit.
        target_posts = node(payload, "spot")["post_bundles"]
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
    assert client.post(ENDPOINT, json=payload).status_code == 200
    payload["spots"] = [{"spot_id": 1, "post_bundles": []}]
    result = client.post(ENDPOINT, json=payload).json()
    assert len(result["spots"]) == 1
    assert result["spots"][0]["result"] == {
        "congestion_level": "UNKNOWN",
        "report_count": 0,
        "causes": [],
    }


def test_analyzer_replacement_preserves_input_and_isolates_spot_failure(
    payload: dict[str, Any],
) -> None:
    class ReplacementAnalyzer:
        def analyze(
            self, spot: CongestionSpotInput, as_of: datetime
        ) -> CongestionResult:
            assert as_of == datetime.fromisoformat("2026-10-07T15:00:00+09:00")
            assert spot.post_bundles[0].title == "현재 상황"
            assert spot.post_bundles[0].content == "  사람이 많아요.  "
            if spot.spot_id == 1:
                raise AnalysisError(
                    "private upstream details must not reach the client"
                )
            assert spot.spot_id == 3  # An empty spot must bypass the analyzer.
            return CongestionResult(congestion_level="LOW", report_count=2, causes=[])

    node(payload, "post")["content"] = "  사람이 많아요.  "
    third = deepcopy(payload["spots"][0])
    third["spot_id"] = 3
    third["post_bundles"][0]["post_id"] = 30
    third["post_bundles"][0]["comments"][0]["comment_id"] = 30
    payload["spots"].append(third)
    app.dependency_overrides[get_analyzer] = ReplacementAnalyzer
    try:
        response = TestClient(app).post(ENDPOINT, json=payload)
    finally:
        app.dependency_overrides.pop(get_analyzer)
    assert response.status_code == 200
    assert response.json() == {
        "request_id": "req-001",
        "spots": [
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
                },
            },
            {
                "spot_id": 3,
                "analysis_status": "SUCCESS",
                "result": {"congestion_level": "LOW", "report_count": 2, "causes": []},
            },
        ],
    }


def test_all_spots_fail_with_fixed_error_and_http_200(payload: dict[str, Any]) -> None:
    class FailingAnalyzer:
        def analyze(
            self, spot: CongestionSpotInput, as_of: datetime
        ) -> CongestionResult:
            raise AnalysisError("private details")

    payload["spots"] = payload["spots"][:1]
    app.dependency_overrides[get_analyzer] = FailingAnalyzer
    try:
        response = TestClient(app).post(ENDPOINT, json=payload)
    finally:
        app.dependency_overrides.pop(get_analyzer)
    assert response.status_code == 200
    assert response.json()["spots"] == [
        {
            "spot_id": 1,
            "analysis_status": "FAILED",
            "result": None,
            "code": "ANALYSIS_FAILED",
            "message": "analysis failed",
        },
    ]


def test_invalid_request_never_reaches_analyzer(payload: dict[str, Any]) -> None:
    class UnexpectedAnalyzer:
        def analyze(
            self, spot: CongestionSpotInput, as_of: datetime
        ) -> CongestionResult:
            pytest.fail("invalid input reached analyzer")

    del payload["spots"][1]["post_bundles"]
    app.dependency_overrides[get_analyzer] = UnexpectedAnalyzer
    try:
        assert_invalid(payload)
    finally:
        app.dependency_overrides.pop(get_analyzer)


def test_malformed_json_uses_common_request_error() -> None:
    response = TestClient(app).post(
        ENDPOINT, content="{", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json() == INVALID
