# S4b 효과 판정 규칙 순서(0으로 나누지 않음)와 값 강제를 확인한다.
from app.Server_Monitor_Bot.ops_agent.adapt.policy import clamp, max_ttl, verdict


def m(calls, fails, p95=None):
    return {"calls": calls, "fails": fails, "fail_rate": fails / calls if calls else 0.0, "p95_ms": p95}


def test_verdict_order():
    assert verdict(m(20, 4), m(4, 0)) == "no_effect"
    assert verdict(m(20, 4, 10000), m(20, 0, 13000)) == "worse"
    assert verdict(m(20, 0), m(20, 3)) == "worse"
    assert verdict(m(20, 0), m(20, 2)) == "no_effect"
    assert verdict(m(10, 2), m(10, 2 * 1.2 // 1 + 1)) == "worse"
    assert verdict(m(10, 1), m(10, 2)) == "no_effect"
    assert verdict(m(20, 8), m(20, 2)) == "improved"
    assert verdict(m(20, 8), m(30, 9)) == "no_effect"


def test_clamp_and_ttl():
    p = {"min": 1, "max": 8, "max_ttl_min": 60}
    assert clamp(20, 90, p, max_ttl(p, "dev"), "dev") == (8, 60)
    assert clamp(1, 90, p, max_ttl(p, "prod"), "prod", baseline=6) == (3, 30)
