# S3 지식 정리 노드: 대상 수집 → 정리본 작성(W2) → knowledge_drafts 저장과 [저장][취소] 확인 요청.
from ...knowledge.schema import STEPS, body
from ..common.env import Env, as_evidence
from ..common.llm import BudgetExceeded
from ..common.schemas import to_item
from ..state import message
from ..tools import work
from ..workers.write_verify import run_write
from . import prompts

OPS = {"knowledge_add": "add", "knowledge_update": "update", "knowledge_delete": "delete"}
LABEL = {"add": "추가", "update": "수정", "delete": "삭제"}


def draft_text(op: str, k: dict, draft_id: str, reason: str, duplicate_of: str | None, ttl_h: int) -> str:
    match = " · ".join(f"{key} {v}" for key, v in (k.get("match") or {}).items())
    lines = [f"📝 지식 {LABEL[op]} 정리본 ({ttl_h}시간 유효, {draft_id})", f"제목: {k.get('title')}", f"신호: {match or '-'}",
             f"원인: {k.get('cause')}", f"해결: {k.get('resolution')}"]
    if k.get("checks"):
        lines.append("확인: " + " / ".join(k["checks"]))
    lines.append(f"이유: {reason}")
    if duplicate_of:
        lines.append(f"비슷한 지식: {duplicate_of}")
    return "\n".join(lines)


class KnowledgeEdit:
    def __init__(self, env: Env):
        self.env = env

    def collect(self, s: dict) -> dict:
        e, repos = s["edit"], self.env.ctx.repos
        history = [{"role": h["role"], "text": h["text"]} for h in repos.chat.recent(e["chat_id"], self.env.agent.get("history_turns", 20))]
        incident = repos.incidents.get(e["incident_id"]) if e.get("incident_id") else None
        target = repos.knowledge.get(e["target_knowledge_id"]) if e.get("target_knowledge_id") else None
        similar = work.knowledge_search(e.get("edit_request") or (target or {}).get("title") or "지식", 3)
        ev = [x for x in (as_evidence(similar, "search_knowledge"),) if x]
        brief = {k: incident.get(k) for k in ("id", "summary", "cause_text", "resolution_text", "step_id", "evidence")} if incident else None
        return {"history": history, "incident": brief, "target": {**body(target), "id": target["id"], "version": target["version"]} if target else None,
                "similar": similar.get("data") if isinstance(similar, dict) else [], "evidence": ev}

    def draft(self, s: dict) -> dict:
        e = s["edit"]
        op = OPS.get(e["intent"], "add")
        inputs = {"op": op, "edit_request": e.get("edit_request"), "대화": s["history"], "장애 기록": s["incident"], "target": s["target"],
                  "비슷한 지식": s["similar"], "steps": list(STEPS), "policy_keys": list(self.env.policy_keys),
                  "active_knowledge_ids": [k["id"] for k in self.env.ctx.repos.knowledge.titles(limit=500)],
                  "known_ids": [f"incident:{s['incident']['id']}"] if s["incident"] else []}
        try:
            out = run_write(self.env.w2, schema="knowledge_draft", instructions=prompts.DRAFT, inputs=inputs, evidence=s["evidence"],
                            max_retries=self.env.agent.get("knowledge_retries", 2), budget=s["budget"])
        except BudgetExceeded:
            out = {"output": None, "valid": False, "errors": ["예산 초과"]}
        return {"result": out, "op": op}

    def confirm(self, s: dict) -> dict:
        e, out, op = s["edit"], s["result"], s["op"]
        if not out["valid"] or not out["output"]:
            reasons = "; ".join(out["errors"][:2]) or "형식 오류"
            return {"outbox": [message(f"정리하지 못했습니다: {reasons}", chat_id=e["chat_id"])], "outcome": "ok"}
        d = out["output"]
        item = to_item(d["knowledge"]) if op != "delete" else {k: v for k, v in (s["target"] or {}).items() if k not in ("id", "version")}
        ttl_h = self.env.agent.get("draft_ttl_h", 24)
        target = s["target"] or {}
        draft_id = self.env.ctx.repos.knowledge.save_draft(
            op=op, target_id=target.get("id"), target_version=target.get("version"), draft_body=item, reason=d["reason"],
            source_refs=d.get("source_refs") or [], incident_id=e.get("incident_id"), chat_id=e["chat_id"], ttl_h=ttl_h)
        text = draft_text(op, item, draft_id, d["reason"], d.get("duplicate_of"), ttl_h)
        buttons = [[("저장", f"ks:{draft_id}"), ("취소", f"kc:{draft_id}")]]
        return {"outbox": [message(text, chat_id=e["chat_id"], buttons=buttons, ref=("draft", draft_id))], "draft_id": draft_id, "outcome": "ok"}
