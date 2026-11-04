# P3 감지: 등급표, 지식 일치에 따른 결정, 즉시 알림·진단 이벤트·정책 신호 발행을 가짜 저장소로 확인한다.
from types import SimpleNamespace

from app.Server_Monitor_Bot.config.settings import load
from app.Server_Monitor_Bot.knowledge.schema import load_seed
from app.Server_Monitor_Bot.pipelines import detector as d

SEED, _ = load_seed()


def item(step, fails, total=10, reason=None, status=None, error_code=None, drill=False, fallback=False):
    return {"key": d.key_of("receipt", step, reason, status, error_code, drill), "drill": drill, "fallback": fallback,
            "signal": {"feature": "receipt", "step_id": step, "reason": reason, "status": status, "error_code": error_code,
                       "ms": 1000, "ratio": fails / total, "fails": fails, "total": total}}


def test_grade_table():
    assert d.grade(item("unhandled", 1)) == d.HIGH
    assert d.grade(item("luna_call", 2)) == d.MEDIUM and d.grade(item("luna_call", 3)) == d.HIGH
    assert d.grade(item("image_fetch", 1)) == d.LOW and d.grade(item("image_fetch", 5)) == d.HIGH
    assert d.grade(item("image_fetch", 2, total=30)) == d.MEDIUM
    assert d.grade(item("request_validate", 9)) == d.LOW
    assert d.grade(item("x", 1, total=30, fallback=True)) == d.LOW
    assert d.grade(item("x", 16, total=30, fallback=True)) == d.HIGH


def test_decide():
    rate = next(k for k in SEED if k["id"] == "KN_OPENAI_RATE_LIMIT")
    low = next(k for k in SEED if k["id"] == "KN_IMAGE_TOO_LARGE")
    diag = next(k for k in SEED if k["id"] == "KN_OPENAI_TIMEOUT")
    assert d.decide(d.LOW, rate, False) == "immediate"
    assert d.decide(d.LOW, low, False) == "none" and d.decide(d.MEDIUM, low, False) == "immediate"
    assert d.decide(d.HIGH, diag, False) == "diagnose" and d.decide(d.HIGH, None, True) == "summary"
    assert d.decide(d.MEDIUM, None, False) == "summary" and d.decide(d.LOW, None, False) == "none"


def test_items_keys():
    window = {"totals": {"receipt": 30}, "groups": [
        {"feature": "receipt", "step_id": "luna_call", "reason": "http_4xx", "status": 429, "error_code": "rate_limit_exceeded",
         "n": 4, "fails": 4, "fallbacks": 0, "max_ms": 9000},
        {"feature": "receipt", "step_id": "image_fetch", "reason": "http_4xx", "status": 403, "error_code": None, "n": 1, "fails": 1,
         "fallbacks": 0, "max_ms": 300},
        {"feature": "receipt", "step_id": "respond", "reason": None, "status": None, "error_code": None, "n": 25, "fails": 0,
         "fallbacks": 0, "max_ms": 9000}]}
    keys = [i["key"] for i in d.items(window, False)]
    assert keys == ["receipt.luna_call.rate_limit_exceeded", "receipt.image_fetch.http_4xx_403"]
    assert d.items(window, True)[0]["key"].startswith("drill.")


class Fake:
    def __init__(self, claim=True):
        self.sent, self.events, self.links, self.claim = [], [], [], claim
        self.repos = SimpleNamespace(
            knowledge=SimpleNamespace(active=lambda: [{**k, "status": "active", "version": 1} for k in SEED]),
            incidents=SimpleNamespace(recent_for_key=lambda key, s: None),
            coord=SimpleNamespace(held=lambda key: 0, claim_alert=lambda *a: self.claim,
                                  link_message=lambda *a: self.links.append(a)),
            metrics=SimpleNamespace(samples=lambda *a, **k: ["rid1"]))
        self.telegram = SimpleNamespace(broadcast=lambda text, buttons=None: self.sent.append(text) or [(5, 77), (6, 78)], text=lambda t: t)
        self.settings, self.task_id = load(), "task-1"

    def enqueue(self, event, not_before=None):
        self.events.append(event)


def test_handle_immediate_with_policy():
    ctx = Fake()
    action = d.Detector(ctx).handle(item("luna_call", 1, total=30, reason="http_4xx", status=429, error_code="rate_limit_exceeded"), {})
    assert action == "immediate" and "KN_OPENAI_RATE_LIMIT" in ctx.sent[0]
    assert [(l[0], l[1], l[2]) for l in ctx.links] == [(77, 5, "alert"), (78, 6, "alert")] and ctx.events[0]["type"] == "signal.policy"
    assert ctx.events[0]["payload"]["messages"] == [[5, 77], [6, 78]]
    assert ctx.events[0]["payload"]["message_id"] == 77 and ctx.events[0]["payload"]["policy_key"] == "llm.max_concurrency"


def test_alert_still_sent_when_sample_lookup_fails():
    ctx = Fake()
    ctx.repos.metrics = SimpleNamespace(samples=lambda *a, **k: 1 / 0)
    action = d.Detector(ctx).handle(item("luna_call", 1, total=30, reason="http_4xx", status=429, error_code="rate_limit_exceeded"), {})
    assert action == "immediate" and len(ctx.sent) == 1 and "샘플:" not in ctx.sent[0]


def test_handle_diagnose_and_claim_lost():
    ctx = Fake()
    assert d.Detector(ctx).handle(item("unhandled", 2, reason="KeyError"), {}) in ("immediate",)
    ctx = Fake()
    assert d.Detector(ctx).handle(item("luna_parse", 5, reason="not_json"), {}) == "diagnose"
    assert ctx.events[0]["type"] == "alert.pipeline" and ctx.events[0]["payload"]["sample_ids"] == ["rid1"]
    lost = Fake(claim=False)
    assert d.Detector(lost).handle(item("luna_parse", 5, reason="not_json"), {}) == "none" and not lost.events


def test_drill_is_blind():
    ctx = Fake()
    ctx.repos.drills = SimpleNamespace(current=lambda: {"id": "R-1", "scenario_id": "S-none"})
    action = d.Detector(ctx).handle(item("luna_call", 4, total=20, reason="http_4xx", status=429,
                                         error_code="rate_limit_exceeded", drill=True), {})
    assert action == "diagnose" and ctx.sent == [] and ctx.links == []
    assert [e["type"] for e in ctx.events] == ["alert.pipeline"]
    assert ctx.events[0]["payload"]["drill"] == {"run_id": "R-1", "hide_knowledge": []}
