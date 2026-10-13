"""진단 출력이 실제 작성자 선택·동률·충돌을 설명하는지 확인한다."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from app.features.congestion import preview
from app.features.congestion.preprocessor import PreparedSpot
from app.features.congestion.relevance import SignalKey

CASE = Path(__file__).resolve().parents[1] / "examples/congestion/spot-preview.json"


@pytest.fixture
def case() -> dict[str, Any]:
    return json.loads(CASE.read_text(encoding="utf-8"))


def diagnose(case: dict[str, Any]) -> dict[str, Any]:
    parsed = preview.PreviewCase.model_validate(case)
    scores = {
        (item.signal_type, item.signal_id): item.probability for item in parsed.scores
    }
    return preview.preview_spot(
        parsed,
        lambda _prepared: scores,
        lambda _prompt: json.dumps(parsed.model_output),
        parsed.relevance_policy,
    )


def test_trace_selects_latest_stage_and_independent_earlier_cause(
    case: dict[str, Any],
) -> None:
    result = diagnose(case)
    trace = result["aggregation_trace"]

    assert trace["stage_counts"] == {"LOW": 1, "MEDIUM": 0, "HIGH": 0}
    assert trace["decision"] == "UNIQUE_TOP"
    assert trace["stage_votes"] == [
        {
            "author_key": "repeat",
            "created_at": "2026-10-12T11:55:00+09:00",
            "status": "COUNTED",
            "sources": [{"signal_type": "post", "signal_id": 3}],
            "congestion_level": "LOW",
        }
    ]
    causes = {vote["author_key"]: vote for vote in trace["cause_votes"]}
    assert causes["repeat"]["sources"] == [{"signal_type": "post", "signal_id": 1}]
    assert causes["repeat"]["causes"] == ["EVENT"]
    assert causes["cause-only"]["sources"] == [
        {"signal_type": "comment", "signal_id": 1}
    ]
    assert causes["cause-only"]["causes"] == ["BREAKDOWN"]
    assert result["aggregate"]["report_count"] == 1


def test_trace_explains_two_author_tie_without_counting_older_stage(
    case: dict[str, Any],
) -> None:
    case["model_output"]["signals"][0]["congestion_level"] = "MEDIUM"
    case["model_output"]["signals"][1]["congestion_level"] = "HIGH"

    result = diagnose(case)
    trace = result["aggregation_trace"]

    assert trace["stage_counts"] == {"LOW": 0, "MEDIUM": 1, "HIGH": 1}
    assert trace["decision"] == "TIE"
    assert [vote["sources"] for vote in trace["stage_votes"]] == [
        [{"signal_type": "post", "signal_id": 3}],
        [{"signal_type": "comment", "signal_id": 1}],
    ]
    assert result["aggregate"]["congestion_level"] == "UNKNOWN"
    assert result["aggregate"]["report_count"] == 2


@pytest.mark.parametrize("conflict", [True, False])
def test_trace_distinguishes_same_time_conflict_from_identical_votes(
    case: dict[str, Any], conflict: bool
) -> None:
    case["request"]["spots"][0]["post_bundles"][1]["created_at"] = (
        "2026-10-12T11:15:00+09:00"
    )
    latest = case["model_output"]["signals"][0]
    latest["congestion_level"] = "LOW" if conflict else "HIGH"
    latest["causes"] = ["DELAY"] if conflict else ["EVENT"]

    result = diagnose(case)
    trace = result["aggregation_trace"]
    stage = trace["stage_votes"][0]

    assert stage["sources"] == [
        {"signal_type": "post", "signal_id": 1},
        {"signal_type": "post", "signal_id": 3},
    ]
    assert stage["status"] == ("CONFLICT" if conflict else "COUNTED")
    assert stage["congestion_level"] == (None if conflict else "HIGH")
    assert trace["stage_counts"] == {
        "LOW": 0,
        "MEDIUM": 0,
        "HIGH": 0 if conflict else 1,
    }
    assert trace["decision"] == ("NO_VALID_STAGE" if conflict else "UNIQUE_TOP")
    causes = {vote["author_key"]: vote for vote in trace["cause_votes"]}
    assert causes["repeat"]["status"] == ("CONFLICT" if conflict else "COUNTED")
    assert causes["repeat"]["causes"] == (None if conflict else ["EVENT"])
    assert result["aggregate"]["report_count"] == (0 if conflict else 1)


def test_trace_has_no_votes_when_classifier_excludes_every_candidate(
    case: dict[str, Any],
) -> None:
    for score in case["scores"]:
        score["probability"] = 0.0

    result = diagnose(case)

    assert result["aggregation_trace"] == {
        "stage_counts": {"LOW": 0, "MEDIUM": 0, "HIGH": 0},
        "decision": "NO_VALID_STAGE",
        "stage_votes": [],
        "cause_votes": [],
    }
    assert result["extraction_calls"] == 0


@pytest.mark.parametrize(
    "arguments",
    [
        ["--request", str(CASE)],
        ["--case", str(CASE), "--spot-id", "7"],
    ],
)
def test_cli_rejects_request_without_live_and_spot_selection_for_mock_case(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["preview", *arguments])
    with pytest.raises(SystemExit) as error:
        preview.main()
    assert error.value.code == 2


@pytest.mark.parametrize("spot_id", [7, 8, 999, None])
def test_live_cli_accepts_http_request_and_requires_unambiguous_spot_selection(
    case: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    spot_id: int | None,
) -> None:
    request = case["request"]
    request["spots"].append({"spot_id": 8, "post_bundles": []})
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(request), encoding="utf-8")
    arguments = [
        "preview",
        "--request",
        str(request_file),
        "--live",
        "--timeout",
        "30",
        "--exclude-below",
        "0.2",
        "--related-at-or-above",
        "0.8",
    ]
    if spot_id is not None:
        arguments.extend(["--spot-id", str(spot_id)])
    monkeypatch.setattr(sys, "argv", arguments)
    calls: list[str] = []

    def score(prepared: PreparedSpot) -> dict[SignalKey, float]:
        calls.append("decisions")
        assert prepared.spot_id == 7
        return {
            (item["signal_type"], item["signal_id"]): item["probability"]
            for item in case["scores"]
        }

    def complete(_prompt: str) -> str:
        calls.append("extraction")
        return json.dumps(case["model_output"])

    monkeypatch.setattr(preview, "DecisionsRelevanceScorer", lambda **_kwargs: score)
    monkeypatch.setattr(preview, "call_api_model", complete)

    exit_code = preview.main()
    output = capsys.readouterr()

    if spot_id in (7, 8):
        assert exit_code == 0, output.stderr
        result = json.loads(output.out)
        assert result["mode"] == "live"
        assert result["request_id"] == "spot-preview-1"
        assert result["spot_id"] == spot_id
        assert result["aggregate"]["report_count"] == (1 if spot_id == 7 else 0)
        assert result["aggregation_trace"]["decision"] == (
            "UNIQUE_TOP" if spot_id == 7 else "NO_VALID_STAGE"
        )
        assert calls == (["decisions", "extraction"] if spot_id == 7 else [])
    else:
        assert exit_code != 0
        assert not output.out
        assert "미리보기 실패" in output.err
        assert calls == []
