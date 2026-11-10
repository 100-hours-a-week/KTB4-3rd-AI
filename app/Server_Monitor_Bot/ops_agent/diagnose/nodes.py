# S1 진단 노드: 컨텍스트 → 조사 요청(W1) → 리포트 요청(W2) → 저장(장애 기록·리포트·훈련 채점).
import json
import math
import time

from ...channels.telegram import render
from ...knowledge.schema import STEPS, signal_text
from ..common.env import Env, as_evidence
from ..common.llm import BudgetExceeded
from ..state import message
from ..tools import raw, work
from ..tools.registry import bundle, hide_knowledge
from ..workers.investigate import run_investigate
from ..workers.write_verify import run_write
from . import prompts


def query(p: dict) -> str:
    sig = p.get("signal") or {}
    match = {"step_id": [p.get("step_id")], "status": [sig.get("status")], "error_code": [sig.get("error_code")], "reason": [p.get("reason")]}
    text = signal_text({k: [x for x in v if x] for k, v in match.items()})
    return f"{p.get('feature') or ''} {text}".strip() or p.get("summary") or "장애"


class Diagnose:
    def __init__(self, env: Env):
        self.env = env

    def context(self, s: dict) -> dict:
        p, drill = s["payload"], s.get("drill") or {}
        hide = drill.get("hide_knowledge") or ()
        feature, step, reason = p.get("feature"), p.get("step_id"), p.get("reason")
        since = time.time() - p["detected_at"] if p.get("detected_at") else 0
        minutes = drill.get("minutes") or min(60, max(5, math.ceil(since / 60) + 5))
        stats = work.step_failures(feature, step, minutes)
        if "error" in stats:
            return {"store_ok": False}
        ev = [as_evidence(stats, "check_step_failures", {"feature": feature, "step_id": step, "minutes": minutes})]
        ev.append(as_evidence(raw.logs(minutes, " ".join(x for x in (step, reason) if x)), "recent_logs", {"minutes": minutes}))
        ev += [as_evidence(raw.trace(rid), "request_trace", {"request_id": rid}) for rid in (p.get("sample_ids") or [])[:5]]
        hits = []
        if drill.get("config") != "single":
            token = hide_knowledge.set(tuple(hide or ()))
            try:
                found = work.knowledge_search(query(p), 5, step_ids=[step] if step else None)
            finally:
                hide_knowledge.reset(token)
            ev.append(as_evidence(found, "search_knowledge"))
            hits = found.get("data") or [] if isinstance(found, dict) else []
        return {"store_ok": True, "evidence": [e for e in ev if e], "knowledge_hits": hits, "hide": list(hide or ())}

    def investigate(self, s: dict) -> dict:
        p, drill = s["payload"], s.get("drill") or {}
        context = json.dumps({"알림": {k: p.get(k) for k in ("alert_key", "summary", "signal", "detected_at")},
                              "맥락": [{"id": e["id"], "summary": e["summary"]} for e in s["evidence"]],
                              "비슷한 지식": [{k: h.get(k) for k in ("id", "title", "cause", "checks")} for h in s["knowledge_hits"]]},
                             ensure_ascii=False, default=str)
        result = run_investigate(self.env.w1, system=prompts.INVESTIGATE, goal="이 장애의 원인 후보를 세우고 근거로 확인",
                                 context=context, tool_set=bundle("S1"), max_rounds=self.env.agent.get("diagnose_rounds", 6),
                                 budget=s["budget"], hide=s.get("hide") or ())
        return {"investigation": result}

    def report(self, s: dict) -> dict:
        p = s["payload"]
        inv = s.get("investigation") or {"evidence": [], "notes": "", "stop_reason": "skipped"}
        evidence = s["evidence"] + inv["evidence"]
        active = [k["id"] for k in self.env.ctx.repos.knowledge.titles(limit=500)]
        inputs = {"alert": {k: p.get(k) for k in ("alert_key", "feature", "step_id", "reason", "summary", "signal", "detected_at")},
                  "steps": list(STEPS), "policy_keys": list(self.env.policy_keys), "active_knowledge_ids": active,
                  "known_ids": [f"req:{r}" for r in p.get("sample_ids") or []], "조사 메모": inv.get("notes", ""),
                  "조사 종료": inv.get("stop_reason")}
        try:
            out = run_write(self.env.w2, schema="diagnosis_report", instructions=prompts.REPORT, inputs=inputs, evidence=evidence,
                            max_retries=self.env.agent.get("diagnose_retries", 2), budget=s["budget"])
        except BudgetExceeded:
            out = {"output": None, "valid": False, "errors": ["예산 초과"], "first_pass": False}
        r = out["output"] or {"summary": p.get("summary") or p.get("alert_key"), "cause_text": "원인 미확정(리포트 작성 실패)",
                              "resolution_text": "로그와 지표를 직접 확인", "step_id": p.get("step_id") if p.get("step_id") in STEPS else "unhandled",
                              "confidence": "low", "evidence_ids": [], "knowledge_ids": [], "policy_key": None, "unknown": True}
        if not out["valid"]:
            tag = "[예산 초과]" if "예산 초과" in out["errors"] else "[근거 검증 실패]"
            r = {**r, "summary": f"{tag} {r['summary']}", "confidence": "low"}
        if inv.get("stop_reason") == "budget" and not r["summary"].startswith("[예산"):
            r = {**r, "summary": f"[예산 초과] {r['summary']}", "confidence": "low"}
        return {"report": r, "first_pass": out.get("first_pass", False), "evidence": evidence}

    def text(self, s: dict, r: dict, drill: bool = False) -> str:
        p = s["payload"]
        return render.diagnosis(r, s["evidence"], p.get("feature"), p.get("detected_at"), drill, p.get("sample_ids"), p.get("reason"))

    def persist(self, s: dict) -> dict:
        p, r, drill = s["payload"], s["report"], s.get("drill") or {}
        repos = self.env.ctx.repos
        is_drill = bool(drill) or bool(p.get("drill")) or str(p.get("alert_key", "")).startswith("drill.")
        incident_id = repos.incidents.create(
            trigger_key=p.get("alert_key", "-"), feature=p.get("feature"), step_id=r["step_id"], summary=r["summary"],
            cause_text=r["cause_text"], resolution_text=r["resolution_text"], confidence=r["confidence"],
            evidence=[{"id": e["id"], "summary": e["summary"]} for e in s["evidence"] if e.get("id") in r["evidence_ids"]],
            knowledge_refs=r["knowledge_ids"], policy_key=r["policy_key"], drill=is_drill, agent_run_id=s.get("run_id"))
        text = self.text(s, r, is_drill)
        outbox = []
        if not is_drill:
            outbox.append(message(text, ref=("incident", incident_id), html=True))
        elif drill.get("config") == "agent" and drill.get("run_no") == 1:
            outbox += [message(text, chat_id=chat, ref=("incident", incident_id), html=True) for chat in self.env.owners()]
        if drill.get("run_no", 0) >= 1:
            expected = drill.get("expected") or {}
            hit, reason = self.env.judge(prompts.JUDGE, expected.get("cause_text", ""), r["cause_text"])
            repos.drills.add_eval({"drill_run_id": drill.get("run_id"), "scenario_id": drill.get("scenario_id"), "group": drill.get("group"),
                                   "config": drill.get("config"), "run_no": drill.get("run_no"), "expected_step_id": expected.get("step_id"),
                                   "predicted_step_id": r["step_id"], "step_hit": r["step_id"] == expected.get("step_id"),
                                   "expected_cause_text": expected.get("cause_text"), "predicted_cause_text": r["cause_text"],
                                   "cause_hit": hit, "judge_reason": reason, "evidence_first_pass": s.get("first_pass"),
                                   "false_positive": False, "duration_ms": s["budget"].elapsed_ms, "tokens": s["budget"].used})
        report = {**r, "incident_id": incident_id, "feature": p.get("feature"), "alert_key": p.get("alert_key"), "message_text": render.plain(text),
                  "drill": is_drill}
        return {"diagnosis_report": report, "outbox": outbox, "outcome": "ok"}
