"""수동 미리보기 명령이 실제 내부 흐름을 실행하고 결과를 보여주는지 확인한다."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "examples/congestion/spot-preview.json"


def run_preview(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY_2", None)
    return subprocess.run(
        [sys.executable, "-m", "app.features.congestion.preview", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_mock_preview_shows_selection_original_metadata_and_explicit_window_aggregate() -> None:
    command = run_preview("--case", str(CASE))

    assert command.returncode == 0, command.stderr
    result = json.loads(command.stdout)
    assert result["mode"] == "mock"
    assert result["extraction_calls"] == 1
    assert result["llm_input"]["targets"] == [
        {"signal_type": "post", "signal_id": 1},
        {"signal_type": "comment", "signal_id": 1},
        {"signal_type": "post", "signal_id": 3},
    ]
    assert [item["status"] for item in result["relevance"]] == [
        "RELATED",
        "UNCERTAIN",
        "UNRELATED",
        "RELATED",
        "UNRELATED",
    ]
    assert result["judgments"][0]["author_key"] == "u1"
    assert result["judgments"][0]["created_at"] == "2026-10-12T11:15:00+09:00"
    assert result["aggregate"] == {
        "congestion_level": "LOW",
        "report_count": 1,
        "causes": [{"cause_code": "BREAKDOWN", "report_count": 1}],
    }
    assert result["summary"] == (
        "제보 기준 혼잡도는 한산이며, 혼잡 제보자는 1명입니다. "
        "제보된 원인은 차량·설비 고장(1명)입니다."
    )


@pytest.mark.parametrize("empty_kind", ["unrelated", "empty_spot"])
def test_preview_without_targets_skips_extraction(tmp_path: Path, empty_kind: str) -> None:
    case = json.loads(CASE.read_text())
    if empty_kind == "unrelated":
        for score in case["scores"]:
            score["probability"] = 0.0
    else:
        case["request"]["spots"][0]["post_bundles"] = []
        case["scores"] = []
    case["model_output"] = {"invalid": "호출되면 안 됨"}
    case_path = tmp_path / "empty.json"
    case_path.write_text(json.dumps(case))

    command = run_preview("--case", str(case_path))

    assert command.returncode == 0, command.stderr
    result = json.loads(command.stdout)
    assert result["extraction_calls"] == 0
    assert result["llm_input"] is None
    assert result["summary"] == "혼잡 단계를 판단할 유효 제보가 없습니다."
    assert result["aggregate"] == {"congestion_level": "UNKNOWN", "report_count": 0, "causes": []}


@pytest.mark.parametrize("failure", ["duplicate_score", "invalid_output", "multiple_spots"])
def test_invalid_preview_case_cannot_show_success(tmp_path: Path, failure: str) -> None:
    case: dict[str, Any] = json.loads(CASE.read_text())
    if failure == "duplicate_score":
        case["scores"].append(case["scores"][0])
    elif failure == "invalid_output":
        case["model_output"]["signals"].pop()
    else:
        case["request"]["spots"].append({"spot_id": 8, "post_bundles": []})
    case_path = tmp_path / "invalid.json"
    case_path.write_text(json.dumps(case))

    command = run_preview("--case", str(case_path))

    assert command.returncode != 0
    assert "미리보기 실패" in command.stderr
    assert not command.stdout


def test_live_preview_requires_explicit_thresholds_and_timeout() -> None:
    command = run_preview("--case", str(CASE), "--live")

    assert command.returncode != 0
    assert "--exclude-below" in command.stderr
    assert "--timeout" in command.stderr
    assert not command.stdout
