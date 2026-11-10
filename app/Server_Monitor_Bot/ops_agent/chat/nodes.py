# S2 챗봇 노드: 준비(압축 문맥·연결 장애·알림 복원·판단 모델·지난 대화·지식·문서·추적을 한 번 임베딩해 병렬로) → 지식 변경 바로 넘김 | 조사(W1) → 답변(W2) → 대화 저장.
import contextvars
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timezone

from ...channels.telegram import render
from ...knowledge.schema import best
from ..common.env import Env, as_evidence
from ..common.evidence import wrap
from ..common.validators import chat_answer as intent_errors
from ..common.llm import BudgetExceeded
from ..state import message
from ..tools import raw, work
from ..tools.registry import bundle
from ..workers.investigate import run_investigate
from ..workers.write_verify import run_write
from . import prompts
from .context import build as context_of

FAILED = render.CHAT_FAILED
DRILL_NOTICE = render.DRILL_NOTICE
KN_ID = re.compile(r"\bKN_[A-Z0-9_]+\b")
SKIP_TOOLS = ("status", "doc", "trace")


def memory_of(rows: list[dict], discord_id) -> list[dict]:
    out = []
    for r in rows:
        ts = r["ts"].replace(tzinfo=timezone.utc).timestamp() if hasattr(r.get("ts"), "replace") else r.get("ts")
        where = "디스코드" if discord_id is not None and r.get("chat_id") == discord_id else "텔레그램"
        out.append({"출처": f"{where} · {render.kst(ts, '%m-%d %H:%M')}", "질문": r["question"], "이전 답(사실 아님)": r["answer"]})
    return out


def parallel(*calls):
    with ThreadPoolExecutor(len(calls)) as pool:
        futures = [pool.submit(contextvars.copy_context().run, fn, *args) for fn, *args in calls]
        return [f.result() for f in futures]


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
        history = context_of(repos.chat, chat_id, self.env.llm, s.get("budget"), self.env.ctx.settings.section("chat"))
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
        question, settings = p.get("text") or "", self.env.ctx.settings
        brief_of = (incident or {}).get("summary") or (alert or {}).get("alert_key") or ""
        try:
            vector = repos.chat.embed.one(question)
        except Exception:
            vector = None
        decide = self.env.decider.route if self.env.decider is not None else (lambda *_: None)
        decision, chats, kn, docs = parallel((decide, question, "\n".join(x for x in (brief_of, history["summary"]) if x)),
                                             (work.chat_search, question, 5, incident["id"] if incident else None, vector),
                                             (work.knowledge_search, question, 5, None, vector),
                                             (work.docs_search, question, settings.section("search").get("docs_prefetch", 3), vector))
        memory = memory_of(chats.get("data") or [] if isinstance(chats, dict) and "error" not in chats else [], settings.discord_channel_id)
        ev = [as_evidence(kn, "search_knowledge"), as_evidence(docs, "search_docs")]
        if decision and decision.get("kind") == "status":
            ev.append(as_evidence(work.overview(60), "check_overview"))
        if alert and isinstance(alert.get("stats"), dict):
            ev.append(as_evidence(alert["stats"], "check_step_failures"))
        if alert and alert.get("knowledge"):
            k = alert["knowledge"]
            ev.append(as_evidence(wrap(f"kn:{k['id']}@v{k['version']}", f"{k['id']} {k['title']}", k), "search_knowledge"))
        if s["type"] == "user.trace" and p.get("request_id"):
            ev.append(as_evidence(raw.trace(p["request_id"]), "request_trace", {"request_id": p["request_id"]}))
        brief = None
        if incident:
            brief = {k: incident.get(k) for k in ("id", "summary", "cause_text", "resolution_text", "step_id", "confidence", "drill")}
            brief["evidence"] = incident.get("evidence")
        return {"history": history, "incident": brief, "alert": {k: v for k, v in (alert or {}).items() if k != "stats"} or None,
                "evidence": [e for e in ev if e], "memory": memory, "decision": decision}

    def next_of(self, s: dict) -> str:
        if s.get("drill_reply"):
            return "drill_notice"
        d = s.get("decision") or {}
        if d.get("intent") == "knowledge_add" or (d.get("intent") in ("knowledge_update", "knowledge_delete") and self.target(s)):
            return "fast_edit"
        if d.get("needs_tools") is not None and d["needs_tools"] < 0.5 and d.get("kind") in SKIP_TOOLS and \
                (d["kind"] != "trace" or s["type"] == "user.trace"):
            return "answer"
        return "investigate"

    def target(self, s: dict) -> str | None:
        active = {k["id"] for k in self.env.ctx.repos.knowledge.titles(limit=500)}
        return next((k for k in KN_ID.findall(s["payload"].get("text") or "") if k in active), None)

    def fast_edit(self, s: dict) -> dict:
        intent = s["decision"]["intent"]
        a = {"answer_text": render.say("fast_edit"), "evidence_ids": [], "intent": intent,
             "target_knowledge_id": self.target(s) if intent != "knowledge_add" else None, "target_doc": None,
             "incident_id": (s["incident"] or {}).get("id"), "edit_request": s["payload"].get("text"), "expected_issues": []}
        return {"answer": a, "evidence": s["evidence"]}

    def drill_notice(self, s: dict) -> dict:
        p = s["payload"]
        return {"chat_answer": {"answer_text": DRILL_NOTICE, "intent": "answer", "evidence_ids": [], "chat_id": s["chat_id"]},
                "outbox": [message(DRILL_NOTICE, chat_id=s["chat_id"], reply_to=p.get("message_id"))], "outcome": "ok"}

    def investigate(self, s: dict) -> dict:
        context = json.dumps({"질문": s["payload"].get("text"), "대화 요약": s["history"]["summary"], "최근 대화": s["history"]["turns"][-10:],
                              "지난 대화(사실 근거 아님)": s.get("memory") or [], "연결된 장애": s["incident"],
                              "연결된 알림": s["alert"], "미리 찾은 것": [{"id": e["id"], "summary": e["summary"]} for e in s["evidence"]]},
                             ensure_ascii=False, default=str)
        result = run_investigate(self.env.w1, system=prompts.INVESTIGATE, goal=s["payload"].get("text") or "", context=context,
                                 tool_set=bundle("S2"), max_rounds=self.env.agent.get("chat_rounds", 4), budget=s["budget"])
        return {"investigation": result}

    def answer(self, s: dict) -> dict:
        inv = s.get("investigation") or {"evidence": [], "notes": ""}
        evidence = s["evidence"] + inv["evidence"]
        inputs = {"질문": s["payload"].get("text"), "대화 요약": s["history"]["summary"], "최근 대화": s["history"]["turns"],
                  "지난 대화(사실 근거 아님)": s.get("memory") or [], "연결된 장애": s["incident"], "연결된 알림": s["alert"],
                  "active_knowledge_ids": [k["id"] for k in self.env.ctx.repos.knowledge.titles(limit=500)],
                  "known_ids": [f"inc:{s['incident']['id']}"] if s["incident"] else [], "조사 메모": inv.get("notes", "")}
        try:
            out = run_write(self.env.w2, schema="chat_answer", instructions=prompts.ANSWER, inputs=inputs, evidence=evidence,
                            max_retries=self.env.agent.get("chat_retries", 1), budget=s["budget"])
        except BudgetExceeded:
            out = {"output": None, "valid": False, "errors": ["예산 초과"]}
        a = out["output"] or {"answer_text": FAILED, "evidence_ids": [], "intent": "answer", "target_knowledge_id": None, "target_doc": None,
                              "incident_id": None, "edit_request": None, "expected_issues": []}
        if out["output"] and not out["valid"]:
            a = {**a, "answer_text": a["answer_text"] + "\n" + render.UNVERIFIED, "expected_issues": [],
                 "intent": "answer" if intent_errors({**a, "evidence_ids": []}, inputs) else a["intent"]}
        if not a.get("incident_id") and s["incident"]:
            a = {**a, "incident_id": s["incident"]["id"]}
        return {"answer": a, "evidence": evidence}

    def save(self, s: dict) -> dict:
        p, a = s["payload"], s["answer"]
        cited = [*a["evidence_ids"], *(e for i in a.get("expected_issues") or [] for e in i.get("evidence_ids") or [])]
        body = render.with_issues(a["answer_text"], a.get("expected_issues")) if a["intent"] == "answer" else a["answer_text"]
        text, html = render.chat_answer(body, s["evidence"], list(dict.fromkeys(cited)), s.get("memory") if "지난 대화" in a["answer_text"] else None)
        row = self.env.ctx.repos.chat.save_turn(chat_id=s["chat_id"], message_id=p.get("message_id"), reply_to=p.get("reply_to_message_id"),
                                                question=p.get("text") or "", answer=render.plain(text) if html else text, incident_id=a.get("incident_id"),
                                                intent=a["intent"], evidence=a["evidence_ids"])
        answer = {**a, "chat_id": s["chat_id"], "message_id": p.get("message_id")}
        return {"chat_answer": answer, "outcome": "ok",
                "outbox": [message(text, chat_id=s["chat_id"], reply_to=p.get("message_id"), ref=("answer", p.get("message_id")), chat_row=row,
                                   html=html)]}
