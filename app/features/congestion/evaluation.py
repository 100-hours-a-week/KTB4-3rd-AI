"""합성 정답셋의 채점. HTTP 실행 경로와 분리한 개발용 함수다."""

from collections import Counter
from typing import Any, get_args

from .extractor import ConcreteCauseCode

STAGES = ["LOW", "MEDIUM", "HIGH", "ABSTAIN"]


def _ratio(correct: int, total: int) -> dict[str, Any]:
    return {
        "correct": correct,
        "total": total,
        "accuracy": correct / total if total else None,
    }


def _classification(pairs: list[tuple[str, str]], labels: list[str]) -> dict[str, Any]:
    counts = Counter(pairs)
    per_class = {}
    for label in labels:
        tp = counts[label, label]
        fp = sum(
            n for (gold, pred), n in counts.items() if pred == label and gold != label
        )
        fn = sum(
            n for (gold, pred), n in counts.items() if gold == label and pred != label
        )
        per_class[label] = _prf(tp, fp, fn)
    return {
        **_ratio(sum(a == b for a, b in pairs), len(pairs)),
        "confusion": {
            gold: {pred: counts[gold, pred] for pred in [*labels, "MISSING"]}
            for gold in labels
        },
        "per_class": per_class,
        "macro_f1": sum(row["f1"] for row in per_class.values()) / len(labels),
    }


def _prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "support": tp + fn,
        "precision": tp / (tp + fp) if tp + fp else 0,
        "recall": tp / (tp + fn) if tp + fn else 0,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0,
    }


def _causes(pairs: list[tuple[set[str], set[str] | None]]) -> dict[str, Any]:
    per_code = {}
    for code in get_args(ConcreteCauseCode):
        tp = sum(
            code in gold and pred is not None and code in pred for gold, pred in pairs
        )
        fp = sum(
            code not in gold and pred is not None and code in pred
            for gold, pred in pairs
        )
        fn = sum(
            code in gold and (pred is None or code not in pred) for gold, pred in pairs
        )
        per_code[code] = _prf(tp, fp, fn)
    return {
        **_ratio(sum(gold == pred for gold, pred in pairs), len(pairs)),
        "missing": sum(pred is None for _, pred in pairs),
        "per_code": per_code,
    }


def _key(item: dict[str, Any]) -> tuple[str, int]:
    return item["signal_type"], item["signal_id"]


def _cause_counts(result: dict[str, Any]) -> dict[str, int]:
    return {item["cause_code"]: item["report_count"] for item in result["causes"]}


def _same_result(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return (
        expected["congestion_level"] == actual.get("congestion_level")
        and expected["report_count"] == actual.get("report_count")
        and _cause_counts(expected) == _cause_counts(actual)
    )


def score_quality(
    cases: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """미실행·실패는 MISSING, 제외된 후보는 조건부 추출 점수에서 제외한다."""
    by_id = {row["case_id"]: row for row in rows}
    case_ids = {case["id"] for case in cases}
    if len(case_ids) != len(cases) or len(by_id) != len(rows) or set(by_id) - case_ids:
        raise ValueError("duplicate or unknown evaluation case ID")
    relevance: list[tuple[str, str]] = []
    selected_stage: list[tuple[str, str]] = []
    pipeline_stage: list[tuple[str, str]] = []
    selected_causes: list[tuple[set[str], set[str] | None]] = []
    pipeline_causes: list[tuple[set[str], set[str] | None]] = []
    aggregate_stage: list[tuple[str, str]] = []
    report_errors: list[int] = []
    cause_errors: list[int] = []
    categories: dict[str, list[bool]] = {}
    errors = []
    exact = failed = excluded_related = 0
    for case in cases:
        row = by_id.get(case["id"], {})
        observed = row.get("observed", {}) if row.get("success") else {}
        if not observed:
            failed += 1
        decisions = {_key(item): item for item in observed.get("relevance", [])}
        judgments = {_key(item): item for item in observed.get("judgments", [])}
        issues = []
        for label in case["annotations"]:
            if not label["in_window"]:
                continue
            key = _key(label)
            decision = decisions.get(key)
            predicted_relevance = (
                ("UNRELATED" if decision["status"] == "UNRELATED" else "RELATED")
                if decision
                else "MISSING"
            )
            relevance.append((label["relevance"], predicted_relevance))
            actual = judgments.get(key)
            gold_stage = label["congestion_level"] or "ABSTAIN"
            predicted_stage = (
                (actual["congestion_level"] or "ABSTAIN") if actual else "MISSING"
            )
            cause_pair = (
                set(label["causes"]),
                set(actual["causes"]) if actual else None,
            )
            if actual is not None:
                selected_stage.append((gold_stage, predicted_stage))
                selected_causes.append(cause_pair)
            if label["relevance"] == "RELATED":
                pipeline_stage.append((gold_stage, predicted_stage))
                pipeline_causes.append(cause_pair)
                excluded_related += predicted_relevance == "UNRELATED"
            if (
                label["relevance"] != predicted_relevance
                or (
                    actual is not None
                    and (
                        gold_stage != predicted_stage or cause_pair[0] != cause_pair[1]
                    )
                )
                or (label["relevance"] == "RELATED" and actual is None)
            ):
                issues.append(
                    {
                        "signal_type": key[0],
                        "signal_id": key[1],
                        "expected": label,
                        "actual": actual,
                        "predicted_relevance": predicted_relevance,
                    }
                )
        expected = case["expected_result"]
        aggregate = observed.get("aggregate")
        aggregate_stage.append(
            (
                expected["congestion_level"],
                aggregate["congestion_level"] if aggregate else "MISSING",
            )
        )
        match = aggregate is not None and _same_result(expected, aggregate)
        exact += match
        categories.setdefault(case["category"], []).append(match)
        if aggregate is not None:
            report_errors.append(
                abs(expected["report_count"] - aggregate["report_count"])
            )
            gold_counts, actual_counts = (
                _cause_counts(expected),
                _cause_counts(aggregate),
            )
            cause_errors.append(
                sum(
                    abs(gold_counts.get(code, 0) - actual_counts.get(code, 0))
                    for code in gold_counts.keys() | actual_counts.keys()
                )
            )
        if issues or not match:
            errors.append(
                {
                    "case_id": case["id"],
                    "category": case["category"],
                    "technical_failure": not bool(observed),
                    "signals": issues,
                    "expected_result": expected,
                    "actual_result": aggregate,
                }
            )
    return {
        "cases": len(cases),
        "recorded_cases": len(rows),
        "failed_or_missing_cases": failed,
        "relevance": _classification(relevance, ["RELATED", "UNRELATED"]),
        "excluded_related": excluded_related,
        "selected_stage": _classification(selected_stage, STAGES),
        "pipeline_stage": _classification(pipeline_stage, STAGES),
        "selected_causes": _causes(selected_causes),
        "pipeline_causes": _causes(pipeline_causes),
        "aggregate_stage": _classification(
            aggregate_stage, ["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
        ),
        "aggregate_exact": _ratio(exact, len(cases)),
        "observed_aggregates": len(report_errors),
        "report_count_mae_on_observed": sum(report_errors) / len(report_errors)
        if report_errors
        else None,
        "cause_count_l1_on_observed": sum(cause_errors) / len(cause_errors)
        if cause_errors
        else None,
        "by_category": {
            name: _ratio(sum(matches), len(matches))
            for name, matches in categories.items()
        },
        "errors": errors,
        "conventions": {
            "relevance": "RELATED + UNCERTAIN are retained as RELATED",
            "macro_f1": "fixed listed classes; zero division is 0",
            "pipeline": "in-window gold RELATED; excluded or failed is MISSING",
            "selected": "only actual extraction judgments, including retained negatives",
            "count_errors": "observed aggregates only; failures stay in aggregate_exact denominator",
        },
    }


def score_load(
    case: dict[str, Any], response: dict[str, Any], status: int
) -> dict[str, Any]:
    """HTTP 200이라도 빠진 스팟·실패 스팟·요청 ID 불일치를 성공으로 세지 않는다."""
    expected = {item["spot_id"]: item for item in case["expected_results"]}
    spots = response.get("spots", [])
    actual = {item["spot_id"]: item for item in spots}
    valid = (
        status == 200
        and response.get("request_id") == case["request"]["request_id"]
        and len(actual) == len(spots)
        and not (actual.keys() - expected.keys())
    )
    successful = exact = 0
    for spot_id, gold in expected.items():
        item = actual.get(spot_id, {}) if valid else {}
        result = item.get("result")
        if item.get("analysis_status") == "SUCCESS" and result is not None:
            successful += 1
            exact += _same_result(gold, result)
    return {
        "success": valid and successful == len(expected),
        "success_spots": successful,
        "exact_spots": exact,
        "total_spots": len(expected),
    }
