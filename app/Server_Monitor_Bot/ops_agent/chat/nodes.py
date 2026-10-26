# S2 챗봇 노드: 준비(대화·연결 장애·알림 복원·기억·지식·추적) → 조사(W1) → 답변(W2) → 대화 저장.
import json

from ...knowledge.schema import best
from ..common.env import Env, as_evidence
from ..common.llm import BudgetExceeded
from ..state import message
from ..tools import raw, work
from ..tools.registry import bundle
from ..workers.investigate import run_investigate
from ..workers.write_verify import run_write
from . import prompts

FAILED = "답을 만들지 못했습니다. 질문을 조금 바꿔 다시 물어봐 주세요."
DRILL_NOTICE = "훈련 장애라 대화·지식 저장 대상이 아닙니다"


def alert_context(env: Env, ref_id: str) -> dict:
    key, _, _at = ref_id.rpartition("@")
    parts = key.removeprefix("drill.").split(".")
    if len(parts) < 2:
        return {"alert_key": key}
    feature, step = parts[0], parts[1]
    stats = work.step_failures(feature, step, 5)
    top = ((stats.get("data") or {}).get("now") or {}).get("reasons") or [{}] if isinstance(stats, dict) else [{}]
    signal = {"feature": feature, "step_id": step, "reason": top[0].get("reason"), "status": top[0].get("status")}
    k = best(env.ctx.repos.knowledge.active(), signal)
    return {"alert_key": key, "feature": feature, "step_id": step, "stats": stats, "signal": signal,
            "knowledge": {f: k[f] for f in ("id", "version", "title", "cause", "resolution", "checks")} if k else None}


class Chat:
    def __init__(self, env: Env):
        self.env = env

    def prepare(self, s: dict) -> dict:
        p, chat_id = s["payload"], s["chat_id"]
        repos = self.env.ctx.repos
        history = [{"role": h["role"], "text": h["text"]} for h in repos.chat.recent(chat_id, self.env.agent.get("history_turns", 20))]
        incident = alert = None
        kind, _, ref_id = (p.get("reply_ref") or "").partition(":")
        if kind == "incident":
            incident = repos.incidents.get(ref_id)
        elif kind == "alert":
            alert = alert_context(self.env, ref_id)
        elif kind == "answer" and ref_id.isdigit():
            iid = repos.chat.incident_of_message(chat_id, int(ref_id))
            incident = repos.incidents.get(iid) if iid else None
        if (incident and incident.get("drill")) or (kind == "alert" and ref_id.startswith("drill.")):
            return {"drill_reply": True}
        question = p.get("text") or ""
        ev = [as_evidence(work.chat_search(question, 5, incident["id"] if incident else None), "search_chat"),
              as_evidence(work.knowledge_search(question, 5), "search_knowledge")]
        if alert and isinstance(alert.get("stats"), dict):
            ev.append(as_evidence(alert["stats"], "check_step_failures"))
        if s["type"] == "user.trace" and p.get("request_id"):
            ev.append(as_evidence(raw.trace(p["request_id"]), "request_trace", {"request_id": p["request_id"]}))
        brief = None
        if incident:
            brief = {k: incident.get(k) for k in ("id", "summary", "cause_text", "resolution_text", "step_id", "confidence", "drill")}
            brief["evidence"] = incident.get("evidence")
        return {"history": history, "incident": brief, "alert": {k: v for k, v in (alert or {}).items() if k != "stats"} or None,
                "evidence": [e for e in ev if e]}

    def drill_notice(self, s: dict) -> dict:
        p = s["payload"]
        return {"chat_answer": {"answer_text": DRILL_NOTICE, "intent": "answer", "evidence_ids": [], "chat_id": s["chat_id"]},
                "outbox": [message(DRILL_NOTICE, chat_id=s["chat_id"], reply_to=p.get("message_id"))], "outcome": "ok"}

    def investigate(self, s: dict) -> dict:
        context = json.dumps({"질문": s["payload"].get("text"), "최근 대화": s["history"][-10:], "연결된 장애": s["incident"],
                              "연결된 알림": s["alert"], "미리 찾은 것": [{"id": e["id"], "summary": e["summary"]} for e in s["evidence"]]},
                             ensure_ascii=False, default=str)
        result = run_investigate(self.env.w1, system=prompts.INVESTIGATE, goal=s["payload"].get("text") or "", context=context,
                                 tool_set=bundle("S2"), max_rounds=self.env.agent.get("chat_rounds", 4), budget=s["budget"])
        return {"investigation": result}

    def answer(self, s: dict) -> dict:
        inv = s.get("investigation") or {"evidence": [], "notes": ""}
        evidence = s["evidence"] + inv["evidence"]
        inputs = {"질문": s["payload"].get("text"), "최근 대화": s["history"], "연결된 장애": s["incident"], "연결된 알림": s["alert"],
                  "active_knowledge_ids": [k["id"] for k in self.env.ctx.repos.knowledge.titles(limit=500)],
                  "known_ids": [f"inc:{s['incident']['id']}"] if s["incident"] else [], "조사 메모": inv.get("notes", "")}
        try:
            out = run_write(self.env.w2, schema="chat_answer", instructions=prompts.ANSWER, inputs=inputs, evidence=evidence,
                            max_retries=self.env.agent.get("chat_retries", 1), budget=s["budget"])
        except BudgetExceeded:
            out = {"output": None, "valid": False, "errors": ["예산 초과"]}
        a = out["output"] or {"answer_text": FAILED, "evidence_ids": [], "intent": "answer", "target_knowledge_id": None,
                              "incident_id": None, "edit_request": None}
        if out["output"] and not out["valid"]:
            a = {**a, "answer_text": a["answer_text"] + "\n(근거 미확인)",
                 "intent": "answer" if a["intent"].startswith("knowledge_") else a["intent"]}
        if not a.get("incident_id") and s["incident"]:
            a = {**a, "incident_id": s["incident"]["id"]}
        return {"answer": a, "evidence": evidence}

    def save(self, s: dict) -> dict:
        p, a = s["payload"], s["answer"]
        cited = {e["id"]: e for e in s["evidence"] if e.get("id")}
        refs = [f"{eid}" for eid in a["evidence_ids"][:3] if eid in cited]
        text = a["answer_text"] + (f"\n근거: {' · '.join(refs)}" if refs else "")
        row = self.env.ctx.repos.chat.save_turn(chat_id=s["chat_id"], message_id=p.get("message_id"), reply_to=p.get("reply_to_message_id"),
                                                question=p.get("text") or "", answer=text, incident_id=a.get("incident_id"),
                                                intent=a["intent"], evidence=a["evidence_ids"])
        answer = {**a, "chat_id": s["chat_id"], "message_id": p.get("message_id")}
        return {"chat_answer": answer, "outcome": "ok",
                "outbox": [message(text, chat_id=s["chat_id"], reply_to=p.get("message_id"), ref=("answer", p.get("message_id")), chat_row=row)]}
