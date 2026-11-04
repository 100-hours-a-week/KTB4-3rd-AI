# S3 지식·문서 정리 노드: 대상 수집 → 정리본 작성(W2) → knowledge_drafts 저장과 [저장][취소] 확인 요청. 문서는 사전 문서 절 정정·삭제.
from ...channels.telegram import render
from ...knowledge.schema import FEATURES, STEPS, body
from ..common.env import Env, as_evidence
from ..common.llm import BudgetExceeded
from ..common.schemas import to_item
from ..state import message
from ..tools import work
from ..workers.write_verify import run_write
from ..chat.context import build as context_of
from . import prompts

OPS = {"knowledge_add": "add", "knowledge_update": "update", "knowledge_delete": "delete", "doc_update": "update", "doc_delete": "delete"}


class KnowledgeEdit:
    def __init__(self, env: Env):
        self.env = env

    def collect(self, s: dict) -> dict:
        e, repos = s["edit"], self.env.ctx.repos
        history = context_of(repos.chat, e["chat_id"], self.env.llm, s.get("budget"), self.env.ctx.settings.section("chat"))
        if str(e.get("intent", "")).startswith("doc_"):
            return {"history": history, **self.doc_target(e)}
        incident = repos.incidents.get(e["incident_id"]) if e.get("incident_id") else None
        target = repos.knowledge.get(e["target_knowledge_id"]) if e.get("target_knowledge_id") else None
        similar = work.knowledge_search(e.get("edit_request") or (target or {}).get("title") or "지식", 3)
        ev = [x for x in (as_evidence(similar, "search_knowledge"),) if x]
        brief = {k: incident.get(k) for k in ("id", "feature", "summary", "cause_text", "resolution_text", "step_id", "evidence")} if incident else None
        return {"history": history, "incident": brief, "target": {**body(target), "id": target["id"], "version": target["version"]} if target else None,
                "similar": similar.get("data") if isinstance(similar, dict) else [], "evidence": ev}

    def doc_target(self, e: dict) -> dict:
        ref = str(e.get("target_doc") or "").removeprefix("doc:")
        doc_id, _, order = ref.rpartition("#")
        chunk = self.env.ctx.repos.docs.chunk(doc_id, int(order)) if order.isdigit() else None
        if not chunk:
            return {"stop": render.say("doc_target")}
        if chunk.get("origin") == "repo":
            return {"stop": render.say("doc_repo", doc_id=doc_id)}
        return {"doc": chunk, "evidence": [{"id": f"doc:{doc_id}#{chunk['order']}", "tool": "search_docs", "args": {},
                                            "summary": f"{doc_id} · {chunk['section']}", "data": chunk["text"][:3000]}]}

    def draft(self, s: dict) -> dict:
        e = s["edit"]
        op = OPS.get(e["intent"], "add")
        if s.get("stop"):
            return {"op": op}
        if s.get("doc"):
            inputs = {"op": op, "edit_request": e.get("edit_request"), "대화": s["history"],
                      "대상 절": {k: s["doc"].get(k) for k in ("doc_id", "section", "order", "text")}}
            try:
                out = run_write(self.env.w2, schema="doc_draft", instructions=prompts.DOC_DRAFT, inputs=inputs, evidence=s["evidence"],
                                max_retries=self.env.agent.get("knowledge_retries", 2), budget=s["budget"])
            except BudgetExceeded:
                out = {"output": None, "valid": False, "errors": ["예산 초과"]}
            return {"result": out, "op": op}
        inputs = {"op": op, "edit_request": e.get("edit_request"), "대화": s["history"], "장애 기록": s["incident"], "target": s["target"],
                  "비슷한 지식": s["similar"], "features": list(FEATURES), "steps": list(STEPS), "policy_keys": list(self.env.policy_keys),
                  "active_knowledge_ids": [k["id"] for k in self.env.ctx.repos.knowledge.titles(limit=500)],
                  "known_ids": [f"incident:{s['incident']['id']}"] if s["incident"] else []}
        try:
            out = run_write(self.env.w2, schema="knowledge_draft", instructions=prompts.DRAFT, inputs=inputs, evidence=s["evidence"],
                            max_retries=self.env.agent.get("knowledge_retries", 2), budget=s["budget"])
        except BudgetExceeded:
            out = {"output": None, "valid": False, "errors": ["예산 초과"]}
        return {"result": out, "op": op}

    def confirm(self, s: dict) -> dict:
        e, op = s["edit"], s["op"]
        if s.get("stop"):
            return {"outbox": [message(s["stop"], chat_id=e["chat_id"])], "outcome": "ok"}
        out = s["result"]
        if not out["valid"] or not out["output"]:
            reasons = "; ".join(out["errors"][:2]) or "형식 오류"
            return {"outbox": [message(render.say("draft_failed", reasons=reasons), chat_id=e["chat_id"])], "outcome": "ok"}
        d = out["output"]
        ttl_h = self.env.agent.get("draft_ttl_h", 24)
        if s.get("doc"):
            old = s["doc"]
            draft_id = self.env.ctx.repos.knowledge.save_draft(
                op=op, target_id=old["key"], target_version=None, draft_body={"text": d.get("text") or "", "doc_id": old["doc_id"],
                                                                             "section": old["section"]},
                reason=d["reason"], source_refs=d.get("source_refs") or [], incident_id=None, chat_id=e["chat_id"], ttl_h=ttl_h, kind="doc")
            text = render.doc_draft(op, old, d.get("text") or "", draft_id, d["reason"], ttl_h)
            buttons = [[("저장", f"ks:{draft_id}"), ("취소", f"kc:{draft_id}")]]
            return {"outbox": [message(text, chat_id=e["chat_id"], buttons=buttons, ref=("draft", draft_id))], "draft_id": draft_id, "outcome": "ok"}
        item = to_item(d["knowledge"]) if op != "delete" else {k: v for k, v in (s["target"] or {}).items() if k not in ("id", "version")}
        target = s["target"] or {}
        draft_id = self.env.ctx.repos.knowledge.save_draft(
            op=op, target_id=target.get("id"), target_version=target.get("version"), draft_body=item, reason=d["reason"],
            source_refs=d.get("source_refs") or [], incident_id=e.get("incident_id"), chat_id=e["chat_id"], ttl_h=ttl_h)
        text = render.knowledge_draft(op, item, draft_id, d["reason"], d.get("duplicate_of"), ttl_h)
        buttons = [[("저장", f"ks:{draft_id}"), ("취소", f"kc:{draft_id}")]]
        return {"outbox": [message(text, chat_id=e["chat_id"], buttons=buttons, ref=("draft", draft_id))], "draft_id": draft_id, "outcome": "ok"}
