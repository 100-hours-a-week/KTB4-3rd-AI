"""모델 호출 없이 입력 구조·정답 집계 정합성·중복·부하 구성을 확인한다."""

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.features.congestion.models import CongestionAnalysisRequest
from app.features.congestion.preview import diagnose_spot
from app.features.congestion.relevance import RelevancePolicy
from app.features.congestion.schema_examples import REQUEST_EXAMPLE

HERE = Path(__file__).resolve().parent
POLICY = RelevancePolicy(0.2, 0.8)


def read(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def contents(value):
    """입력 본문만 모으고 ID·시각 차이는 중복 판정 근거로 사용하지 않는다."""
    if isinstance(value, dict):
        for name, item in value.items():
            if name == "content" and isinstance(item, str):
                yield " ".join(item.split())
            else:
                yield from contents(item)
    elif isinstance(value, list):
        for item in value:
            yield from contents(item)


def replay(request, spot_id, annotations):
    by_key = {(item["signal_type"], item["signal_id"]): item for item in annotations}
    assert len(by_key) == len(annotations), "duplicate annotation ID"
    expected = [
        {
            "signal_type": item["signal_type"],
            "signal_id": item["signal_id"],
            "congestion_level": item["congestion_level"],
            "causes": item["causes"],
        }
        for item in annotations
        if item["in_window"] and item["relevance"] == "RELATED"
    ]

    def score(prepared):
        candidates = {
            (item.signal_type, item.signal_id) for item in prepared.candidates
        }
        assert candidates == {key for key, item in by_key.items() if item["in_window"]}
        return {
            key: 1.0 if by_key[key]["relevance"] == "RELATED" else 0.0
            for key in candidates
        }

    return diagnose_spot(
        request,
        score,
        lambda _prompt: json.dumps({"signals": expected}),
        POLICY,
        spot_id=spot_id,
    )


def main():
    quality = read("quality.json")
    loads = read("load.json")
    manifest = read("manifest.json")
    for name, expected_hash in manifest["sha256"].items():
        assert (
            hashlib.sha256((HERE / name).read_bytes()).hexdigest() == expected_hash
        ), name
    unique = list(contents([case["request"] for case in quality]))
    assert len(unique) == len(set(unique)), "duplicate quality content"
    previous = set(contents(REQUEST_EXAMPLE))
    historical_paths = [
        "docs/congestion/archive/evaluations/first-live/cases.json",
        "docs/congestion/archive/evaluations/consistency/cases.json",
        "tests/fixtures/congestion/parent-cause-regression.json",
        "examples/congestion/http-request.json",
    ]
    for name in historical_paths:
        previous.update(contents(json.loads((ROOT / name).read_text(encoding="utf-8"))))
    overlap = sorted(set(unique) & previous)
    assert not overlap, overlap
    stages = Counter()
    relevance = Counter()
    for case in quality:
        request = CongestionAnalysisRequest.model_validate(case["request"])
        spot = request.spots[0]
        actual_ids = {("post", post.post_id) for post in spot.post_bundles}
        actual_ids.update(
            ("comment", comment.comment_id)
            for post in spot.post_bundles
            for comment in post.comments
        )
        assert actual_ids == {
            (item["signal_type"], item["signal_id"]) for item in case["annotations"]
        }
        observed = replay(request, spot.spot_id, case["annotations"])
        assert observed["aggregate"] == case["expected_result"], case["id"]
        stages[case["expected_result"]["congestion_level"]] += 1
        for item in case["annotations"]:
            relevance[item["relevance"] or "OUTSIDE_WINDOW"] += 1
    load_stats = []
    for load in loads:
        request = CongestionAnalysisRequest.model_validate(load["request"])
        count = sum(
            1 + len(post.comments)
            for spot in request.spots
            for post in spot.post_bundles
        )
        assert count == load["signal_count"]
        expected_results = {item["spot_id"]: item for item in load["expected_results"]}
        author_keys = []
        for spot in request.spots:
            annotations = []
            for post in spot.post_bundles:
                author_keys.append(post.author_key)
                annotations.append(
                    {
                        "signal_type": "post",
                        "signal_id": post.post_id,
                        "in_window": True,
                        "relevance": "RELATED",
                        "congestion_level": "MEDIUM",
                        "causes": ["EVENT"],
                    }
                )
                for offset, comment in enumerate(post.comments, start=1):
                    author_keys.append(comment.author_key)
                    annotations.append(
                        {
                            "signal_type": "comment",
                            "signal_id": comment.comment_id,
                            "in_window": True,
                            "relevance": "RELATED" if offset <= 2 else "UNRELATED",
                            "congestion_level": "MEDIUM"
                            if offset == 1
                            else "HIGH"
                            if offset == 2
                            else None,
                            "causes": [],
                        }
                    )
            observed = replay(request, spot.spot_id, annotations)
            expected = {
                name: value
                for name, value in expected_results[spot.spot_id].items()
                if name != "spot_id"
            }
            assert observed["aggregate"] == expected, (load["id"], spot.spot_id)
        assert len(author_keys) == len(set(author_keys)) == count
        load_stats.append(
            {
                "signals": count,
                "spots": len(request.spots),
                "bundles": count // 5,
                "expected_decisions_calls": count // 5,
                "expected_extraction_calls": 6,
            }
        )
    verification = {
        "quality_cases_valid": len(quality),
        "quality_signals": len(unique),
        "internal_exact_duplicate_contents": 0,
        "historical_exact_duplicate_contents": len(overlap),
        "expected_spot_stage_counts": dict(stages),
        "annotation_relevance_counts": dict(relevance),
        "oracle_aggregate_checks_passed": len(quality)
        + sum(len(load["request"]["spots"]) for load in loads),
        "load_profiles": load_stats,
        "actual_model_calls": 0,
        "meaning": "structure_and_oracle_aggregation_only_not_model_accuracy_or_human_label_approval",
    }
    (HERE / "validation.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(verification, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
