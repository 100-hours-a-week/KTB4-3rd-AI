# 메인 그래프 M: 검문 → 배분 → 서브에이전트 호출 → 연쇄 → 발행. 판단하지 않는다. run_event가 진입점.
import logging
import time
import uuid
from datetime import date

from langgraph.graph import END, START, StateGraph

from . import events
from .actions import Actions
from .adapt.apply_graph import build_apply
from .adapt.propose_graph import build_propose
from .chat.graph import build_chat
from .common.env import Env
from .common.llm import Budget, make_chat
from .diagnose.graph import build_diagnose
from .knowledge.graph import build_knowledge
from .state import MainState, message
from .tools import registry
from .workers.investigate import build_investigate
from .workers.write_verify import build_write_verify

log = logging.getLogger("app.Server_Monitor_Bot.agent")
CALLS = ("diagnose", "chat", "knowledge", "propose", "apply")
EDIT_INTENTS = ("knowledge_add", "knowledge_update", "knowledge_delete")
TELEGRAM_LIMIT = 4096
_budget_notice: dict[str, date] = {}


def is_drill_event(payload: dict) -> bool:
    return bool(payload.get("drill")) or str(payload.get("alert_key", "")).startswith("drill.")


def route(s: MainState) -> dict:
    t = s["event"]["type"]
    return {"route": "action" if t.startswith("action.") else events.ROUTES[t], "node_path": s.get("node_path", []) + ["route"]}


def build_main(nodes):
    g = StateGraph(MainState)
    for n in ("ingest", "action", "chain", "publish", *CALLS):
        g.add_node(n, getattr(nodes, n))
    g.add_node("route", getattr(nodes, "route", route))
    g.add_edge(START, "ingest")
    g.add_conditional_edges("ingest", lambda s: ("end" if s.get("drop_reason") in ("duplicate", "unknown_type") else "publish")
                            if s.get("drop") else "route", {"end": END, "publish": "publish", "route": "route"})
    g.add_conditional_edges("route", lambda s: s["route"], {**{n: n for n in CALLS}, "action": "action"})
    g.add_edge("diagnose", "chain")
    g.add_edge("chat", "chain")
    g.add_conditional_edges("chain", nodes.chain_target, {"propose": "propose", "knowledge": "knowledge", "publish": "publish"})
    for n in ("knowledge", "propose", "apply", "action"):
        g.add_edge(n, "publish")
    g.add_edge("publish", END)
    return g.compile()


def step(s: MainState, name: str, **update) -> dict:
    return {**update, "node_path": s.get("node_path", []) + [name]}


class Nodes:
    def __init__(self, ctx, llm):
        self.ctx = ctx
        self.env = Env(ctx, llm, build_investigate(llm, registry.tools()), build_write_verify(llm))
        self.graphs = {"diagnose": build_diagnose(self.env), "chat": build_chat(self.env), "knowledge": build_knowledge(self.env),
                       "propose": build_propose(self.env), "apply": build_apply(self.env)}
        self.actions = Actions(self.env)

    def notice(self, event: dict, text: str) -> list[dict]:
        return [message(text, chat_id=event.get("chat_id"))] if event.get("chat_id") or event.get("source") != "telegram" else []

    def ingest(self, s: MainState) -> dict:
        e, settings, repos = s["event"], self.ctx.settings, self.ctx.repos
        t, payload = e.get("type", ""), e.get("payload") or {}
        drop = lambda reason, text=None: step(s, "ingest", drop=True, drop_reason=reason,
                                              outbox=self.notice(e, text) if text else [])
        if not events.known(t):
            return drop("unknown_type")
        if repos.coord.seen_event(e["id"]):
            return drop("duplicate")
        if t.startswith("action.drill") and t != "action.drill_guess" and not settings.drill_allowed:
            return drop("stage", f"{settings.stage}에서는 훈련을 할 수 없습니다")
        if t == "action.drill_guess" and not settings.drill_allowed:
            return drop("stage", f"{settings.stage}에서는 훈련을 할 수 없습니다")
        if not settings.feature(events.FEATURE.get(t, "F9")):
            return drop("disabled", f"이 기능은 {settings.stage}에서 꺼져 있습니다")
        tg = settings.section("telegram")
        role = None
        if e.get("source") == "telegram":
            uid = e.get("from_id")
            role = "owner" if uid in (tg.get("owner_ids") or []) else "drill_operator" if uid in (tg.get("drill_operator_ids") or []) else None
            if role is None or (role == "drill_operator" and t not in events.DRILL_OPERATOR_OK):
                return drop("permission", "권한이 없습니다")
        drill = payload.get("drill") if isinstance(payload.get("drill"), dict) else None
        b = settings.section("budget")
        is_drill = is_drill_event(payload)
        if events.ROUTES.get(t) in events.LLM_ROUTES:
            limit = b.get("drill_daily_tokens" if is_drill else "daily_tokens", 300000)
            if repos.coord.tokens_today(is_drill) >= limit:
                key, today = f"{settings.stage}:{is_drill}", date.today()
                text = None
                if _budget_notice.get(key) != today:
                    _budget_notice[key] = today
                    text = (f"오늘 모니터 토큰 상한에 도달했습니다. 감지 결과만 보냅니다: {payload.get('alert_key', t)} "
                            f"{(payload.get('signal') or {}).get('count', '-')}/{(payload.get('signal') or {}).get('calls', '-')}")
                return drop("budget", text)
        tokens = {"single": b.get("single_tokens", 6000), "rag": b.get("rag_tokens", 8000)}.get((drill or {}).get("config"), b.get("run_tokens", 30000))
        budget = Budget(tokens, b.get("run_ms", 120000), b.get("reserve_tokens", 5000), b.get("reserve_ms", 25000))
        if t == "alert.pipeline" and payload.get("detected_at") and time.time() - payload["detected_at"] > 60:
            budget.started -= min(time.time() - payload["detected_at"], budget.ms / 1000)
        return step(s, "ingest", drop=False, role=role, budget=budget, budget_tokens=tokens, budget_ms=b.get("run_ms", 120000),
                    drill_config=drill, run_no=(drill or {}).get("run_no", 0), chain_depth=e.get("chain_depth", 0),
                    from_id=e.get("from_id"), chat_id=e.get("chat_id"), source=e.get("source"))

    def route(self, s: MainState) -> dict:
        return route(s)

    def diagnose(self, s: MainState) -> dict:
        p = s["event"]["payload"]
        token = registry.drill_mode.set(is_drill_event(p))
        try:
            out = self.graphs["diagnose"].invoke({"run_id": s["run_id"], "payload": p, "drill": s.get("drill_config"), "budget": s["budget"]})
        finally:
            registry.drill_mode.reset(token)
        return step(s, "diagnose", diagnosis_report=out.get("diagnosis_report"), outbox=s.get("outbox", []) + out.get("outbox", []),
                    sub_result={"route": "S1", "outcome": out.get("outcome", "ok")})

    def chat(self, s: MainState) -> dict:
        e = s["event"]
        out = self.graphs["chat"].invoke({"type": e["type"], "payload": e["payload"], "chat_id": e.get("chat_id"), "budget": s["budget"]})
        return step(s, "chat", chat_answer=out.get("chat_answer"), outbox=s.get("outbox", []) + out.get("outbox", []),
                    sub_result={"route": "S2", "outcome": out.get("outcome", "ok")})

    def chain_target(self, s: MainState) -> str:
        r, a = s.get("diagnosis_report"), s.get("chat_answer")
        if r and not r.get("drill") and r.get("policy_key") in self.ctx.settings.policy:
            return "propose"
        if a and a.get("intent") in EDIT_INTENTS:
            return "knowledge"
        return "publish"

    def chain(self, s: MainState) -> dict:
        r, a = s.get("diagnosis_report"), s.get("chat_answer")
        target = self.chain_target(s)
        if target == "propose":
            return step(s, "chain", adjust_request={"policy_key": r["policy_key"], "incident_id": r["incident_id"],
                                                    "knowledge_id": (r.get("knowledge_ids") or [None])[0], "feature": r.get("feature"),
                                                    "step_id": r.get("step_id"), "source": "diagnosis", "summary": r.get("summary")})
        if target == "knowledge":
            return step(s, "chain", edit_request={"intent": a["intent"], "edit_request": a.get("edit_request"),
                                                  "target_knowledge_id": a.get("target_knowledge_id"), "incident_id": a.get("incident_id"),
                                                  "chat_id": a.get("chat_id")})
        return step(s, "chain")

    def knowledge(self, s: MainState) -> dict:
        edit = s.get("edit_request") or {**(s["event"].get("payload") or {}), "chat_id": s["event"].get("chat_id")}
        out = self.graphs["knowledge"].invoke({"edit": edit, "budget": s["budget"]})
        return step(s, "knowledge", outbox=s.get("outbox", []) + out.get("outbox", []),
                    sub_result={**s.get("sub_result", {}), "route": s.get("sub_result", {}).get("route", "S3"), "knowledge": out.get("outcome", "ok")})

    def propose(self, s: MainState) -> dict:
        req = s.get("adjust_request")
        if not req:
            p = s["event"]["payload"]
            sig = p.get("signal") or {}
            req = {**p, "feature": sig.get("feature"), "step_id": sig.get("step_id"), "source": "alert" if p.get("message_id") else "signal",
                   "summary": p.get("alert_key")}
        out = self.graphs["propose"].invoke({"request": req, "budget": s["budget"]})
        outbox = list(s.get("outbox", []))
        prop = out.get("proposal")
        if prop:
            ref = ("adjustment", prop["adj_id"])
            idx = next((i for i in range(len(outbox) - 1, -1, -1) if (outbox[i].get("ref") or ("",))[0] == "incident"), None)
            if prop["source"] == "diagnosis" and idx is not None:
                outbox[idx] = {**outbox[idx], "text": outbox[idx]["text"] + "\n" + prop["paragraph"], "buttons": prop["buttons"]}
            elif prop["source"] == "alert" and prop.get("message_id"):
                outbox.append(message(prop["paragraph"], buttons=prop["buttons"], ref=ref, edit_message_id=prop["message_id"],
                                      edit_text=f"{prop.get('message_text') or ''}\n{prop['paragraph']}".strip()))
            else:
                outbox.append(message(f"정책 신호: {req.get('summary') or req.get('policy_key')}\n{prop['paragraph']}", buttons=prop["buttons"], ref=ref))
        return step(s, "propose", outbox=outbox, sub_result={**s.get("sub_result", {}), "route": s.get("sub_result", {}).get("route", "S4a")})

    def apply(self, s: MainState) -> dict:
        e = s["event"]
        out = self.graphs["apply"].invoke({"type": e["type"], "payload": e["payload"], "chat_id": e.get("chat_id")})
        return step(s, "apply", outbox=s.get("outbox", []) + out.get("outbox", []), followups=s.get("followups", []) + out.get("followups", []),
                    sub_result={"route": "S4b", "outcome": out.get("outcome", "ok")})

    def action(self, s: MainState) -> dict:
        out = self.actions.handle(s["event"])
        return step(s, "action", outbox=s.get("outbox", []) + out.get("outbox", []), followups=s.get("followups", []) + out.get("followups", []),
                    sub_result={"route": "action", "outcome": "ok"})

    def send(self, m: dict) -> int | None:
        tg = self.ctx.telegram
        if tg is None:
            return None
        if m.get("edit_message_id") and m.get("edit_text") is not None:
            text = m["edit_text"]
            if len(text) <= TELEGRAM_LIMIT and tg.edit(m.get("chat_id"), m["edit_message_id"], text, m.get("buttons")):
                return m["edit_message_id"]
            return tg.send(m.get("chat_id"), m["text"], m.get("buttons"), reply_to=m["edit_message_id"])
        return tg.send(m.get("chat_id"), m["text"], m.get("buttons"), reply_to=m.get("reply_to"))

    def publish(self, s: MainState) -> dict:
        repos, e = self.ctx.repos, s["event"]
        default_chat = self.ctx.settings.section("telegram").get("chat_id") or None
        for m in s.get("outbox", []):
            try:
                mid = self.send(m)
            except Exception as exc:
                log.warning("publish failed: error=%s", type(exc).__name__)
                mid = None
            if mid is None:
                self.ctx.stats["send_fail"] = self.ctx.stats.get("send_fail", 0) + 1
                continue
            chat = m.get("chat_id") or default_chat
            if m.get("ref") and chat:
                repos.coord.link_message(mid, chat, m["ref"][0], m["ref"][1])
            if m.get("chat_row"):
                repos.chat.fill_bot_message(m["chat_row"], mid)
        for f in s.get("followups", []):
            ev = f["event"]
            ev.setdefault("chain_depth", 0)
            ev["chain_depth"] = max(ev["chain_depth"], s.get("chain_depth", 0) + 1)
            self.ctx.enqueue(ev, f.get("not_before"))
        record(self.ctx, s, "dropped" if s.get("drop") else s.get("sub_result", {}).get("outcome", "ok"))
        return step(s, "publish")


def record(ctx, s: dict, outcome: str, error: str | None = None) -> None:
    e, b = s["event"], s.get("budget")
    p = e.get("payload") or {}
    try:
        ctx.repos.coord.record_run({
            "run_id": s["run_id"], "event_id": e["id"], "event_type": e.get("type", "-"),
            "route": events.SUB_NAME.get(s.get("route", ""), s.get("route")), "drill": is_drill_event(p),
            "node_path": s.get("node_path", []), "input_tokens": b.input_tokens if b else 0, "output_tokens": b.output_tokens if b else 0,
            "tokens": b.used if b else 0, "ms": int((time.time() - s["started_at"]) * 1000), "tool_ms": b.tool_ms if b else 0,
            "detect_to_send_ms": int((time.time() - p["detected_at"]) * 1000) if e.get("type") == "alert.pipeline" and p.get("detected_at") else None,
            "outcome": outcome, "error": error, "image_tag": ctx.image_tag})
    except Exception as exc:
        log.warning("record run failed: error=%s", type(exc).__name__)


def runtime(ctx):
    rt = getattr(ctx, "_ops_runtime", None)
    if rt is None:
        registry.bind(ctx)
        llm = getattr(ctx, "llm", None) or make_chat(ctx.settings)
        nodes = Nodes(ctx, llm)
        rt = (nodes, build_main(nodes))
        ctx._ops_runtime = rt
    return rt


def run_event(ctx, event: dict) -> dict:
    state = {"run_id": uuid.uuid4().hex[:16], "event": event, "started_at": time.time(), "outbox": [], "followups": [], "node_path": []}
    try:
        nodes, graph = runtime(ctx)
        out = graph.invoke(state, {"recursion_limit": 50})
        return {"run_id": state["run_id"], "outcome": "dropped" if out.get("drop") else out.get("sub_result", {}).get("outcome", "ok"),
                "drop_reason": out.get("drop_reason"), "node_path": out.get("node_path", [])}
    except Exception as exc:
        log.exception("agent run failed: type=%s error=%s", event.get("type"), type(exc).__name__)
        record(ctx, {**state, "route": events.ROUTES.get(event.get("type", ""))}, "error", type(exc).__name__)
        return {"run_id": state["run_id"], "outcome": "error", "error": type(exc).__name__}
