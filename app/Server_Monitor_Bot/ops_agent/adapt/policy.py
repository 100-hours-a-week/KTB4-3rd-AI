# 조정 정책 도우미: 현재 값, 값 강제(클램프), 효과 판정 규칙(0으로 나누지 않는 순서).
import math


def current_value(flags: list[dict], knob: str, policy: dict):
    for f in flags:
        if f["key"] == knob:
            return f["value"]
    return policy.get("default", 0)


def clamp(value, ttl_min: int, policy: dict, max_ttl: int, stage: str, baseline: int | float | None = None) -> tuple[int, int]:
    v = max(policy["min"], min(policy["max"], int(value)))
    if stage == "prod" and baseline:
        v = max(v, min(policy["max"], math.ceil(baseline * 0.5)))
    return v, max(1, min(int(ttl_min or max_ttl), max_ttl))


def max_ttl(policy: dict, stage: str) -> int:
    return policy["max_ttl_min"] // 2 if stage == "prod" else policy["max_ttl_min"]


def verdict(before: dict, after: dict) -> str:
    if after.get("calls", 0) < 5:
        return "pending"
    bp, ap = before.get("p95_ms"), after.get("p95_ms")
    if bp and ap is not None and ap >= bp * 1.3:
        return "worse"
    br, ar = before.get("fail_rate", 0.0), after.get("fail_rate", 0.0)
    if not br:
        return "worse" if after.get("fails", 0) >= 3 else "no_effect"
    change = (ar - br) / br
    if change >= 0.10 and (after["calls"] >= 20 or after.get("fails", 0) >= 3):
        return "worse"
    if change <= -0.20 and after.get("fails", 0) < before.get("fails", 0):
        return "improved"
    return "no_effect"
