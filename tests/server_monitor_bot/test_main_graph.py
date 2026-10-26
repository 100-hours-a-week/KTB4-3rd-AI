# 메인 그래프 라우팅·연쇄(스텁 노드)와 검문(가짜 저장소)을 확인한다.
from types import SimpleNamespace

from app.Server_Monitor_Bot.config.settings import load
from app.Server_Monitor_Bot.ops_agent.main_graph import Nodes, build_main
from app.Server_Monitor_Bot.runtime.context import Ctx, new_event

from .fakes import FakeChat


class Stub:
    def __init__(self, report=None, answer=None):
        self.report, self.answer = report, answer

    def _n(self, name, **kw):
        return lambda s: {"node_path": s.get("node_path", []) + [name], **kw}

    def __getattr__(self, name):
        if name == "route":
            raise AttributeError(name)
        extra = {"diagnose": {"diagnosis_report": self.report}, "chat": {"chat_answer": self.answer}}.get(name, {})
        return self._n(name, **extra)

    def ingest(self, s):
        return {"node_path": ["ingest"], "drop": s["event"]["type"] == "bad", "drop_reason": "unknown_type"}

    def chain_target(self, s):
        if (s.get("diagnosis_report") or {}).get("policy_key"):
            return "propose"
        if (s.get("chat_answer") or {}).get("intent", "").startswith("knowledge_"):
            return "knowledge"
        return "publish"


def path(stub, type_):
    return build_main(stub).invoke({"event": {"type": type_, "payload": {}}, "node_path": []})["node_path"]


def test_routes_and_chains():
    assert path(Stub(report={"policy_key": "llm.max_concurrency"}), "alert.pipeline") == ["ingest", "route", "diagnose", "chain", "propose", "publish"]
    assert path(Stub(report={"policy_key": None}), "alert.pipeline") == ["ingest", "route", "diagnose", "chain", "publish"]
    assert path(Stub(answer={"intent": "knowledge_add"}), "user.message") == ["ingest", "route", "chat", "chain", "knowledge", "publish"]
    assert path(Stub(), "action.status") == ["ingest", "route", "action", "publish"]
    assert path(Stub(), "adapt.observe") == ["ingest", "route", "apply", "publish"]
    assert path(Stub(), "bad") == ["ingest"]


class FakeCoord:
    def __init__(self, seen=(), tokens=0):
        self.seen, self.tokens = set(seen), tokens

    def seen_event(self, eid):
        return eid in self.seen

    def tokens_today(self, drill):
        return self.tokens


def nodes(stage="prod", owners=(1,), operators=(2,), seen=(), tokens=0):
    settings = load({"APP_STAGE": stage})
    settings.cfg["telegram"] = {**settings.cfg["telegram"], "owner_ids": list(owners), "drill_operator_ids": list(operators)}
    ctx = Ctx(settings=settings, repos=SimpleNamespace(coord=FakeCoord(seen, tokens)))
    return Nodes(ctx, FakeChat())


def ingest(n, type_, **kw):
    ev = new_event(type_, kw.pop("payload", {}), **kw)
    return n.ingest({"event": ev, "node_path": []})


def test_ingest_rules():
    n = nodes()
    assert ingest(n, "action.drill_start", source="telegram", from_id=2, chat_id=9)["drop_reason"] == "stage"
    assert ingest(n, "user.message", source="telegram", from_id=2, chat_id=9)["drop_reason"] == "permission"
    assert ingest(n, "user.message", source="telegram", from_id=3, chat_id=9)["drop_reason"] == "permission"
    ok = ingest(n, "user.message", source="telegram", from_id=1, chat_id=9)
    assert ok["drop"] is False and ok["role"] == "owner" and ok["budget"].tokens == 30000
    assert ingest(nodes(seen={"x"}), "action.status", event_id="x")["drop_reason"] == "duplicate"
    assert ingest(n, "nope")["drop_reason"] == "unknown_type"
    dev = nodes(stage="dev")
    assert ingest(dev, "action.drill_start", source="telegram", from_id=2, chat_id=9)["drop"] is False
    capped = ingest(nodes(tokens=10**9), "alert.pipeline", payload={"alert_key": "receipt.luna_call.x"})
    assert capped["drop_reason"] == "budget" and "토큰 상한" in capped["outbox"][0]["text"]
    flag = ingest(dev, "alert.pipeline", payload={"alert_key": "drill.receipt.luna_call.x", "drill": True})
    assert flag["drop"] is False and flag["drill_config"] is None and flag["run_no"] == 0
    detector = ingest(dev, "alert.pipeline", payload={"alert_key": "drill.receipt.luna_call.x", "drill": {"run_id": "R-1", "hide_knowledge": ["KN_A"]}})
    assert detector["drill_config"]["hide_knowledge"] == ["KN_A"] and detector["run_no"] == 0
    drill = ingest(dev, "alert.pipeline", payload={"drill": {"config": "single", "run_no": 1}})
    assert drill["budget"].tokens == 6000 and drill["run_no"] == 1


def test_propose_attaches_to_report_or_alert():
    n = nodes()
    prop = {"adj_id": "A-1", "paragraph": "⚙️ 제안", "buttons": [[("승인", "aa:A-1")]], "source": "diagnosis", "message_id": None, "message_text": None}
    n.graphs["propose"] = SimpleNamespace(invoke=lambda s: {"proposal": prop})
    s = {"event": {"type": "alert.pipeline", "payload": {}}, "adjust_request": {"policy_key": "llm.max_concurrency"}, "budget": None,
         "outbox": [{"text": "리포트", "ref": ("incident", "I-1"), "buttons": None}], "node_path": []}
    out = n.propose(s)["outbox"]
    assert len(out) == 1 and out[0]["text"].endswith("⚙️ 제안") and out[0]["buttons"]
    prop2 = {**prop, "source": "alert", "message_id": 77, "message_text": "알림 본문"}
    n.graphs["propose"] = SimpleNamespace(invoke=lambda s: {"proposal": prop2})
    out = n.propose({"event": {"type": "signal.policy", "payload": {"policy_key": "llm.max_concurrency", "message_id": 77,
                                                                     "message_text": "알림 본문", "signal": {}}}, "budget": None, "outbox": [], "node_path": []})["outbox"]
    assert out[0]["edit_message_id"] == 77 and out[0]["edit_text"] == "알림 본문\n⚙️ 제안"
