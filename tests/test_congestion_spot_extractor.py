"""스팟별 단일 호출과 개별 단계·원인 판정의 검증을 확인한다."""

import json
from datetime import datetime
from typing import Any

import pytest

from app.core.exceptions import UpstreamError
from app.features.congestion.extractor import ModelOutputInvalid
from app.features.congestion.preprocessor import CandidateSignal
from app.features.congestion.spot_extractor import assess_spot
from app.features.congestion.spot_input import SpotLLMInput


@pytest.fixture
def model_input() -> SpotLLMInput:
    return SpotLLMInput(
        payload=json.dumps(
            {
                "spot_id": 7,
                "as_of": "2026-10-12T12:00:00+09:00",
                "window_start": "2026-10-12T11:00:00+09:00",
                "targets": [
                    {"signal_type": "comment", "signal_id": 3},
                    {"signal_type": "post", "signal_id": 3},
                ],
                "post_bundles": [],
            }
        ),
        targets=(
            CandidateSignal(
                "comment",
                3,
                1,
                None,
                "행사 때문에 줄이 길어요.",
                "repeat",
                datetime.fromisoformat("2026-10-12T11:15:00+09:00"),
            ),
            CandidateSignal(
                "post",
                3,
                3,
                "현재 상황",
                "지금 한산해요.",
                "repeat",
                datetime.fromisoformat("2026-10-12T11:55:00+09:00"),
            ),
        ),
    )


def output() -> dict[str, Any]:
    return {
        "signals": [
            {"signal_type": "post", "signal_id": 3, "congestion_level": "LOW", "causes": []},
            {
                "signal_type": "comment",
                "signal_id": 3,
                "congestion_level": "MEDIUM",
                "causes": ["EVENT"],
            },
        ],
    }


def test_one_call_matches_reordered_results_to_original_targets(model_input: SpotLLMInput) -> None:
    prompts: list[str] = []

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps(output(), ensure_ascii=False)

    result = assess_spot(model_input, complete)

    assert len(prompts) == 1
    assert json.loads(prompts[0].split("입력 JSON:\n", 1)[1]) == json.loads(model_input.payload)
    assert [
        (signal.author_key, signal.created_at.isoformat(), signal.congestion_level, signal.causes)
        for signal in result.signals
    ] == [
        ("repeat", "2026-10-12T11:15:00+09:00", "MEDIUM", frozenset({"EVENT"})),
        ("repeat", "2026-10-12T11:55:00+09:00", "LOW", frozenset()),
    ]


@pytest.mark.parametrize("case", ["missing", "duplicate", "extra", "context", "wrong_type"])
def test_target_mismatch_rejects_entire_spot(model_input: SpotLLMInput, case: str) -> None:
    body = output()
    if case == "missing":
        body["signals"].pop()
    elif case == "duplicate":
        body["signals"].append(body["signals"][0])
    elif case == "extra":
        body["signals"].append(dict(body["signals"][0], signal_id=99))
    elif case == "context":
        body["signals"][1]["signal_type"] = "post"
        body["signals"][1]["signal_id"] = 1
    else:
        body["signals"][1]["signal_type"] = "reply"
    with pytest.raises(ModelOutputInvalid):
        assess_spot(model_input, lambda _: json.dumps(body))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("congestion_level", "UNKNOWN"),
        ("causes", ["UNKNOWN"]),
        ("causes", ["EVENT", "EVENT"]),
        ("causes", ["QUEUE"]),
        ("signal_id", "3"),
        ("signal_id", True),
        ("author_key", "model-author"),
        ("created_at", "2026-10-12T12:00:00+09:00"),
    ],
)
def test_invalid_signal_fields_are_rejected(
    model_input: SpotLLMInput, field: str, value: Any
) -> None:
    body = output()
    body["signals"][0][field] = value
    with pytest.raises(ModelOutputInvalid):
        assess_spot(model_input, lambda _: json.dumps(body))


@pytest.mark.parametrize("case", ["invalid_json", "missing_signals", "extra_field", "non_text"])
def test_invalid_spot_envelope_is_rejected(model_input: SpotLLMInput, case: str) -> None:
    body = output()
    if case == "missing_signals":
        body.pop("signals")
    elif case == "extra_field":
        body["report_count"] = 999
    raw: Any = json.dumps(body)
    if case == "invalid_json":
        raw = "```json\n{}\n```"
    elif case == "non_text":
        raw = body
    with pytest.raises(ModelOutputInvalid):
        assess_spot(model_input, lambda _: raw)


@pytest.mark.parametrize("has_cause", [False, True])
def test_no_stage_supports_cause_only_or_no_valid_evidence(
    model_input: SpotLLMInput, has_cause: bool
) -> None:
    body = output()
    for signal in body["signals"]:
        signal["congestion_level"] = None
        signal["causes"] = []
    body["signals"][0]["causes"] = ["BREAKDOWN"] if has_cause else []

    result = assess_spot(model_input, lambda _: json.dumps(body))

    assert all(signal.congestion_level is None for signal in result.signals)
    assert result.signals[1].causes == (frozenset({"BREAKDOWN"}) if has_cause else frozenset())


def test_empty_input_skips_model_call() -> None:
    def unexpected(_prompt: str) -> str:
        pytest.fail("빈 대상에 모델을 호출함")

    result = assess_spot(None, unexpected)
    assert result.signals == ()


def test_upstream_failure_is_not_retried(model_input: SpotLLMInput) -> None:
    calls = 0

    def fail(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        raise UpstreamError()

    with pytest.raises(UpstreamError):
        assess_spot(model_input, fail)
    assert calls == 1
