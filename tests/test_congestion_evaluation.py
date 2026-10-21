"""평가에서 누락·제외·기술 실패를 정답으로 세지 않는지 확인한다."""

from copy import deepcopy
from typing import Any

import pytest

from app.features.congestion.evaluation import score_load, score_quality


def sample() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    case = {
        "id": "one",
        "category": "direct_stage",
        "annotations": [
            {
                "signal_type": "post",
                "signal_id": 1,
                "in_window": True,
                "relevance": "RELATED",
                "congestion_level": "LOW",
                "causes": [],
            },
            {
                "signal_type": "comment",
                "signal_id": 2,
                "in_window": True,
                "relevance": "UNRELATED",
                "congestion_level": None,
                "causes": [],
            },
        ],
        "expected_result": {
            "congestion_level": "LOW",
            "report_count": 1,
            "causes": [{"cause_code": "UNKNOWN", "report_count": 0}],
        },
    }
    row = {
        "case_id": "one",
        "success": True,
        "observed": {
            "relevance": [
                {"signal_type": "post", "signal_id": 1, "status": "UNCERTAIN"},
                {"signal_type": "comment", "signal_id": 2, "status": "UNRELATED"},
            ],
            "judgments": [
                {
                    "signal_type": "post",
                    "signal_id": 1,
                    "congestion_level": "LOW",
                    "causes": [],
                },
            ],
            "aggregate": deepcopy(case["expected_result"]),
        },
    }
    return [case], [row]


def test_uncertain_is_retained_and_excluded_negative_is_not_a_correct_llm_null() -> (
    None
):
    cases, rows = sample()
    score = score_quality(cases, rows)
    assert score["relevance"]["accuracy"] == 1
    assert score["selected_stage"]["total"] == 1
    assert score["selected_causes"]["total"] == 1
    assert score["pipeline_stage"]["accuracy"] == 1
    assert score["aggregate_exact"]["accuracy"] == 1


@pytest.mark.parametrize("rows", [[], [{"case_id": "one", "success": False}]])
def test_failure_or_unfinished_case_remains_in_denominator(
    rows: list[dict[str, Any]],
) -> None:
    cases, _ = sample()
    score = score_quality(cases, rows)
    assert score["cases"] == 1
    assert score["failed_or_missing_cases"] == 1
    assert score["aggregate_exact"] == {"correct": 0, "total": 1, "accuracy": 0}
    assert score["pipeline_stage"]["confusion"]["LOW"]["MISSING"] == 1
    assert score["selected_stage"]["total"] == 0
    assert score["relevance"]["total"] == 2
    assert score["report_count_mae_on_observed"] is None


def test_filtered_related_null_does_not_count_as_a_correct_extraction() -> None:
    cases, rows = sample()
    cases[0]["annotations"][0]["congestion_level"] = None
    rows[0]["observed"]["relevance"][0]["status"] = "UNRELATED"
    rows[0]["observed"]["judgments"] = []
    score = score_quality(cases, rows)
    assert score["excluded_related"] == 1
    assert score["pipeline_stage"]["confusion"]["ABSTAIN"]["MISSING"] == 1
    assert score["selected_stage"]["total"] == 0
    assert score["pipeline_causes"]["correct"] == 0


def test_cause_false_positive_and_count_error_are_visible() -> None:
    cases, rows = sample()
    rows[0]["observed"]["judgments"][0]["causes"] = ["EVENT"]
    rows[0]["observed"]["aggregate"]["report_count"] = 3
    rows[0]["observed"]["aggregate"]["causes"] = [
        {"cause_code": "EVENT", "report_count": 2}
    ]
    score = score_quality(cases, rows)
    assert score["selected_causes"]["per_code"]["EVENT"]["fp"] == 1
    assert score["report_count_mae_on_observed"] == 2
    assert score["cause_count_l1_on_observed"] == 2
    assert score["aggregate_exact"]["correct"] == 0
    assert score["errors"][0]["case_id"] == "one"


def test_duplicate_or_unknown_case_result_is_rejected() -> None:
    cases, rows = sample()
    with pytest.raises(ValueError):
        score_quality(cases, rows + rows)
    rows[0]["case_id"] = "unknown"
    with pytest.raises(ValueError):
        score_quality(cases, rows)


def test_load_missing_spot_and_http_error_are_failures() -> None:
    case = {
        "request": {"request_id": "load"},
        "expected_results": [
            {
                "spot_id": 1,
                "congestion_level": "MEDIUM",
                "report_count": 3,
                "causes": [],
            },
            {
                "spot_id": 2,
                "congestion_level": "MEDIUM",
                "report_count": 3,
                "causes": [],
            },
        ],
    }
    response = {
        "request_id": "load",
        "spots": [
            {
                "spot_id": 1,
                "analysis_status": "SUCCESS",
                "result": {
                    "congestion_level": "MEDIUM",
                    "report_count": 3,
                    "causes": [],
                    "summary": "요약",
                },
            },
        ],
    }
    score = score_load(case, response, 200)
    assert score["success_spots"] == 1
    assert score["exact_spots"] == 1
    assert score["total_spots"] == 2
    assert score["success"] is False
    assert score_load(case, response, 500)["success_spots"] == 0


def test_runner_saves_failure_and_keeps_all_cases_in_partial_score(
    tmp_path: Any,
) -> None:
    from app.features.congestion.evaluate import run_quality
    from app.features.congestion.relevance import RelevancePolicy

    cases, _ = sample()
    cases[0]["request"] = {
        "request_id": "test",
        "as_of": "2026-10-13T18:00:00+09:00",
        "window_minutes": 10,
        "spots": [
            {
                "spot_id": 1,
                "post_bundles": [
                    {
                        "post_id": 1,
                        "title": "현장",
                        "content": "한산해요",
                        "author_key": "a",
                        "created_at": "2026-10-13T17:59:00+09:00",
                        "comments": [],
                    }
                ],
            }
        ],
    }

    def fail(_prepared: Any) -> Any:
        raise ValueError("키나 원문을 담을 수도 있는 오류 메시지")

    rows = run_quality(cases, tmp_path, fail, lambda _: "", RelevancePolicy(0.2, 0.8))
    assert rows[0]["success"] is False
    assert rows[0]["error_types"] == ["ValueError"]
    assert "오류 메시지" not in (tmp_path / "quality-results.json").read_text()
    import json

    summary = json.loads((tmp_path / "quality-summary.json").read_text())
    assert summary["aggregate_exact"]["total"] == 1
    assert summary["aggregate_exact"]["correct"] == 0


def test_freeze_rejects_changed_dataset_and_existing_output(tmp_path: Any) -> None:
    import hashlib
    import json

    from app.features.congestion.evaluate import freeze

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "quality.json").write_text("[]")
    (dataset / "manifest.json").write_text(
        json.dumps({"sha256": {"quality.json": "bad"}})
    )
    with pytest.raises(ValueError, match="hash"):
        freeze(dataset, tmp_path / "run", {})
    assert not (tmp_path / "run").exists()
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "sha256": {
                    "quality.json": hashlib.sha256(b"[]").hexdigest(),
                }
            }
        )
    )
    freeze(dataset, tmp_path / "run", {})
    assert not list((tmp_path / "run" / "source").rglob("test_*.py"))
    with pytest.raises(FileExistsError):
        freeze(dataset, tmp_path / "run", {})
    assert (tmp_path / "run" / "quality.json").read_text() == "[]"


@pytest.mark.parametrize("successful,exact", [(False, 0), (True, 5)])
def test_last_load_failure_cannot_exit_successfully(
    tmp_path: Any,
    monkeypatch: Any,
    successful: bool,
    exact: int,
) -> None:
    import json
    import sys

    from app.features.congestion import evaluate

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "load.json").write_text(json.dumps([{"signal_count": 30}]))
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setenv("OPENAI_API_KEY_2", "test-key")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate",
            "--dataset",
            str(dataset),
            "--output",
            str(output),
            "--mode",
            "load",
            "--sizes",
            "30",
            "--repeats",
            "1",
        ],
    )
    monkeypatch.setattr(evaluate, "freeze", lambda *_args: {})
    monkeypatch.setattr(
        evaluate,
        "run_load",
        lambda *_args: [
            {
                "success": successful,
                "exact_spots": exact,
                "total_spots": 6,
            }
        ],
    )
    assert evaluate.main() == 1
    metadata = json.loads((output / "metadata.json").read_text())
    assert metadata["stopped_on_failure"] is True


def test_model_call_observer_records_usage_and_transport_failure_without_raw_text() -> (
    None
):
    import httpx

    from app.features.congestion.evaluate import ModelCalls

    calls = ModelCalls()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            request=request,
            json={"model": "test", "usage": {"input_tokens": 5}, "output": "비밀 원문"},
        )
    )
    with httpx.Client(transport=transport) as client:
        request = client.build_request(
            "POST",
            "https://api.openai.com/v1/responses",
            headers={"Authorization": "Bearer test-key"},
        )
        calls.send(client, request)
        assert calls.rows[0]["status"] == 200
        assert calls.rows[0]["usage"] == {"input_tokens": 5}
        assert "비밀" not in str(calls.rows)
        assert "test-key" not in str(calls.rows)

        def fail(*_args: Any, **_kwargs: Any) -> Any:
            raise httpx.ReadTimeout("비밀 원문")

        calls.original = fail
        with pytest.raises(httpx.ReadTimeout):
            calls.send(client, request)
        assert calls.rows[1]["status"] is None
        assert calls.rows[1]["error_type"] == "ReadTimeout"
        assert calls.rows[1]["seconds"] >= 0
        assert "비밀" not in str(calls.rows)
