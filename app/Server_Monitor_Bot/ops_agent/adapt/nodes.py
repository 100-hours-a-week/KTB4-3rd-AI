# S4a 제안(정책 매칭 → W2 제안·가드 → 승인 요청)과 S4b 적용·재측정·판정·원복·보고 노드.
import json
import time

from langchain_core.messages import HumanMessage, SystemMessage

from ...runtime.context import new_event
from ...store.repos.base import dt, epoch
from ..common.env import Env, as_evidence, hm
from ..common.llm import BudgetExceeded
from ..state import message
from ..tools import work
from ..workers.write_verify import run_write
from . import policy as pol
from . import prompts

V3_ONLY = ("route.force_luna", "modal.wait_s")
WORDS = {"improved": "유지", "no_effect": "효과 없음", "worse": "악화 → 원래 값으로 되돌렸습니다"}
ICON = {"improved": "✅", "no_effect": "➖", "worse": "⚠️"}


def fmt(knob: str, value) -> str:
    if value in (0, None, False) and knob == "llm.max_concurrency":
        return "제한 없음"
    return str(value)


def device() -> str:
    from ....core.config import DEVICE

    return DEVICE


class Propose:
    def __init__(self, env: Env):
        self.env = env

    def match(self, s: dict) -> dict:
        r, ctx = s["request"], self.env.ctx
        knob = r.get("policy_key")
        p = ctx.settings.policy.get(knob)
        if not p:
            return {"skip": "정책 없음"}
        if knob in V3_ONLY and device() == "off":
            return {"skip": "이 환경에서 의미 없음"}
        if ctx.stage == "prod" and not p.get("prod_ok", True):
            return {"skip": "prod 금지"}
        if ctx.repos.adjust.active_for_knob(knob):
            return {"skip": "같은 knob 활성"}
        current = pol.current_value(ctx.repos.adjust.flags(), knob, p)
        stats = work.step_failures(r.get("feature") or "receipt", r.get("step_id") or "luna_call", 30)
        res = work.resources(30)
        inflight = max([int(x.get("inflight_max") or 0) for x in (res.get("data") or [])] or [0]) if isinstance(res, dict) else 0
        baseline = current if current else inflight
        return {"policy": p, "current": current, "baseline": baseline,
                "evidence": [e for e in (as_evidence(stats, "check_step_failures"), as_evidence(res, "check_resources")) if e]}

    def propose(self, s: dict) -> dict:
        r, p, stage = s["request"], s["policy"], self.env.ctx.stage
        ttl_max = pol.max_ttl(p, stage)
        inputs = {"knob": r["policy_key"], "policy": {"min": p["min"], "max": p["max"]}, "current": s["current"], "baseline": s["baseline"],
                  "max_ttl_min": ttl_max, "stage": stage, "summary": r.get("summary"), "signal": r.get("signal")}
        try:
            out = run_write(self.env.w2, schema="adjust_proposal", instructions=prompts.PROPOSE, inputs=inputs, evidence=s["evidence"],
                            max_retries=self.env.agent.get("propose_retries", 1), budget=s["budget"])
        except BudgetExceeded:
            out = {"output": None, "valid": False}
        o = out["output"] or {"value": max(p["min"], int(s["baseline"] or 0) // 2), "ttl_min": ttl_max,
                              "reason": "기본 제안(작성 실패)", "evidence_ids": []}
        value, ttl = pol.clamp(o["value"], o["ttl_min"], p, ttl_max, stage, s["baseline"])
        return {"value": value, "ttl": ttl, "reason": o["reason"]}

    def request(self, s: dict) -> dict:
        r, repos, knob = s["request"], self.env.ctx.repos, s["request"]["policy_key"]
        adj_id = repos.adjust.propose(incident_id=r.get("incident_id"), knowledge_id=r.get("knowledge_id"), knob=knob,
                                      old_value=s["current"], new_value=s["value"], ttl_min=s["ttl"])
        repos.adjust.set_status(adj_id, "proposed", before_metrics={"target": {"feature": r.get("feature"), "step_id": r.get("step_id")}})
        paragraph = f"⚙️ 조정 제안: {knob} {fmt(knob, s['current'])} → {s['value']} ({s['ttl']}분)\n근거: {s['reason']}"
        buttons = [[("승인", f"aa:{adj_id}"), ("거절", f"ar:{adj_id}")]]
        return {"proposal": {"adj_id": adj_id, "paragraph": paragraph, "buttons": buttons, "source": r.get("source"),
                             "message_id": r.get("message_id"), "message_text": r.get("message_text")}, "outcome": "ok"}


class Apply:
    def __init__(self, env: Env, retry_waits=(1, 2, 4)):
        self.env, self.retry_waits = env, retry_waits

    def apply(self, s: dict) -> dict:
        repos, adj_id = self.env.ctx.repos, s["payload"].get("adj_id")
        adj = repos.adjust.get(adj_id)
        if not adj or adj["status"] != "proposed":
            return {"outbox": [message(f"이미 처리했거나 없는 조정입니다({adj_id})", chat_id=s.get("chat_id"))], "outcome": "ok"}
        target = (adj.get("before_metrics") or {}).get("target") or {}
        now = time.time()
        before = repos.metrics.compare(target.get("feature") or "receipt", target.get("step_id") or "luna_call", dt(now - 600), dt(now))
        value = True if adj["knob"] == "route.force_luna" else adj["new_value"]
        expires = now + adj["ttl_min"] * 60
        for wait in (*self.retry_waits, None):
            try:
                repos.adjust.apply(adj_id, adj["knob"], value, expires, {"target": target, **before})
                break
            except Exception:
                if wait is None:
                    return {"outbox": [message("적용하지 못했습니다. 잠시 뒤 [승인]을 다시 눌러 주세요", chat_id=s.get("chat_id"))],
                            "outcome": "error"}
                time.sleep(wait)
        after_s = self.env.agent.get("observe_after_s", 600)
        return {"followups": [{"event": new_event("adapt.observe", {"adj_id": adj_id}, source="followup"), "not_before": now + after_s}],
                "outbox": [message(f"적용됨: {adj['knob']} {fmt(adj['knob'], adj['old_value'])} → {adj['new_value']} "
                                   f"({adj['ttl_min']}분, 만료 {hm(expires)}). {after_s // 60}분 뒤 효과를 보고합니다",
                                   chat_id=s.get("chat_id"), ref=("adjustment", adj_id))], "outcome": "ok"}

    def observe(self, s: dict) -> dict:
        repos, adj_id = self.env.ctx.repos, s["payload"].get("adj_id")
        adj = repos.adjust.get(adj_id)
        if not adj or adj["status"] != "applied" or adj.get("verdict"):
            return {"skip": True}
        before = adj.get("before_metrics") or {}
        target = before.get("target") or {}
        start = epoch(adj["applied_at"])
        after = repos.metrics.compare(target.get("feature") or "receipt", target.get("step_id") or "luna_call", dt(start), dt(start + 600))
        return {"adj": adj, "before": before, "after": after}

    def conclude(self, s: dict) -> dict:
        v = pol.verdict(s["before"], s["after"])
        line = None
        try:
            msg = self.env.llm.invoke([SystemMessage(content=prompts.SUMMARY),
                                       HumanMessage(content=json.dumps({"판정": v, "전": s["before"], "후": s["after"]}, ensure_ascii=False, default=str))])
            line = msg.content if isinstance(msg.content, str) else None
        except Exception:
            pass
        return {"verdict": v, "summary_line": line}

    def rollback(self, s: dict) -> dict:
        self.env.ctx.repos.adjust.rollback(s["adj"]["id"], s["adj"]["knob"])
        return {}

    def report(self, s: dict) -> dict:
        adj, b, a, v = s["adj"], s["before"], s["after"], s["verdict"]
        status = {"improved": "kept", "no_effect": "applied", "worse": "rolled_back"}[v]
        self.env.ctx.repos.adjust.set_status(adj["id"], status, verdict=v, after_metrics=a)
        target = b.get("target") or {}
        def p95(x):
            return f"{x / 1000:.0f}s" if x else "-"
        text = (f"{ICON[v]} 조정 효과 ({hm()}): {target.get('feature')} {target.get('step_id')} 실패 {b.get('fails', 0)}/{b.get('calls', 0)} → "
                f"{a['fails']}/{a['calls']}, p95 {p95(b.get('p95_ms'))} → {p95(a.get('p95_ms'))} → {WORDS[v]}")
        if v == "improved":
            text += f" ({hm(epoch(adj['applied_at']) + adj['ttl_min'] * 60)} 자동 원복)"
        if s.get("summary_line"):
            text += f"\n{s['summary_line']}"
        buttons = [[("즉시 원복", f"ab:{adj['id']}")]] if v == "no_effect" else None
        return {"outbox": [message(text, buttons=buttons, ref=("adjustment", adj["id"]))], "outcome": "ok"}
