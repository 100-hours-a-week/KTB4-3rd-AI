# 장애 지식 항목 검사와 신호(match) 판정. P3·P4·W2·[저장]이 같이 쓴다.
import hashlib
import json
import re
from pathlib import Path

STEPS = ("request_validate", "image_fetch", "image_validate", "model_ready", "pipe_ocr", "pipe_judge",
         "luna_call", "luna_parse", "respond", "unhandled", "queue_wait", "congestion_judge")
FEATURES = ("receipt", "taxi_meter", "congestion")
MATCH_KEYS = ("step_id", "status", "error_code", "reason", "head", "min_ms", "min_ratio")
ALERTS = ("immediate", "diagnose")
SEVERITIES = {"high": 3, "medium": 2, "low": 1}
ID = re.compile(r"^KN_[A-Z0-9_]{2,28}$")
SEED = Path(__file__).with_name("seed.json")
FIELDS = ("title", "features", "step_ids", "match", "symptom", "cause", "resolution", "checks", "policy_key", "alert", "severity")


def validate(item: dict, policy_keys) -> list[str]:
    errors = []
    if not ID.match(str(item.get("id", ""))):
        errors.append(f"id 형식: {item.get('id')}")
    for f in ("title", "symptom", "cause", "resolution"):
        if not str(item.get(f) or "").strip():
            errors.append(f"{f} 비어 있음")
    match = item.get("match")
    if not isinstance(match, dict):
        errors.append("match는 객체")
        match = {}
    for k, v in match.items():
        if k not in MATCH_KEYS:
            errors.append(f"match 키 허용 안 됨: {k}")
        elif k in ("min_ms", "min_ratio"):
            if not isinstance(v, (int, float)):
                errors.append(f"match.{k}는 수")
        elif not isinstance(v, list) or not v:
            errors.append(f"match.{k}는 값이 있는 목록")
    if any(s not in STEPS for s in match.get("step_id", [])):
        errors.append("match.step_id에 모르는 단계")
    if any(s not in STEPS for s in item.get("step_ids") or []):
        errors.append("step_ids에 모르는 단계")
    if any(f not in FEATURES for f in item.get("features") or []):
        errors.append(f"features에 모르는 기능(쓸 수 있는 값: {', '.join(FEATURES)})")
    if not isinstance(item.get("checks", []), list):
        errors.append("checks는 목록")
    if item.get("policy_key") not in (None, *policy_keys):
        errors.append(f"policy_key가 정책 표에 없음: {item.get('policy_key')}")
    if item.get("alert") not in ALERTS:
        errors.append("alert는 immediate|diagnose")
    if item.get("severity") not in SEVERITIES:
        errors.append("severity는 high|medium|low")
    return errors


def matches(match: dict, signal: dict) -> int | None:
    if not match:
        return None
    for key, want in match.items():
        if key == "min_ms":
            if (signal.get("ms") or 0) < want:
                return None
        elif key == "min_ratio":
            if (signal.get("ratio") or 0) < want:
                return None
        elif key == "head":
            if not set(signal.get("heads") or []) & set(want):
                return None
        elif signal.get(key) not in want:
            return None
    return len(match)


def best(items: list[dict], signal: dict) -> dict | None:
    found = []
    for item in items:
        if item.get("status", "active") != "active":
            continue
        if item.get("features") and signal.get("feature") not in item["features"]:
            continue
        score = matches(item.get("match") or {}, signal)
        if score is not None:
            width = sum(len(v) for v in item["match"].values() if isinstance(v, list))
            found.append((score, -width, SEVERITIES.get(item.get("severity"), 0), item))
    if not found:
        return None
    return max(found, key=lambda x: x[:3])[3]


def signal_text(match: dict) -> str:
    words = {"step_id": "단계", "status": "HTTP 상태", "error_code": "오류 코드", "reason": "사유", "head": "로그"}
    parts = [f"{words[k]} {', '.join(map(str, v))}" for k, v in match.items() if k in words and v]
    if match.get("min_ms"):
        parts.append(f"지연 {match['min_ms']}ms 이상")
    if match.get("min_ratio"):
        parts.append(f"실패 비율 {match['min_ratio']:.0%} 이상")
    return ", ".join(parts)


def body(item: dict) -> dict:
    return {k: item.get(k) for k in FIELDS}


def load_seed(path: Path = SEED) -> tuple[list[dict], str]:
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()[:12]
