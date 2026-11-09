# 관리자 여러 명: 먼저 누른 사람만 반영(조건부 갱신)되고, 같은 건으로 보낸 모든 메시지의 버튼이 처리 결과로 바뀐다.
import time
from types import SimpleNamespace

from app.Server_Monitor_Bot.channels.telegram import render
from app.Server_Monitor_Bot.channels.telegram.receiver import to_event
from app.Server_Monitor_Bot.ops_agent.actions import Actions
from app.Server_Monitor_Bot.ops_agent.adapt.nodes import Apply
from app.Server_Monitor_Bot.ops_agent.state import message
from app.Server_Monitor_Bot.runtime.context import new_event

from .test_main_graph import nodes

COPIES = [(1, 77), (2, 78)]


class FakeCoord:
    def messages_of(self, kind, ref_id, hours=None):
        return COPIES if (kind, ref_id) in (("adjustment", "A-1"), ("draft", "D-1")) else []


class FakeAdjust:
    def __init__(self, status="proposed"):
        self.status, self.flags = status, []

    def get(self, adj_id):
        return {"id": adj_id, "knob": "llm.max_concurrency", "status": self.status, "old_value": 0, "new_value": 3, "ttl_min": 30,
                "before_metrics": {"target": {"feature": "receipt", "step_id": "luna_call"}}} if adj_id == "A-1" else None

    def set_status(self, adj_id, status, expect=None, **fields):
        if expect and self.status not in expect:
            return 0
        self.status = status
        return 1

    def apply(self, adj_id, knob, value, expires, before):
        if self.status != "proposed":
            return False
        self.status = "applied"
        self.flags.append(knob)
        return True

    def rollback(self, adj_id, knob, status="rolled_back"):
        if self.status not in ("applied", "kept"):
            return False
        self.status = status
        return True


class FakeKnowledge:
    def __init__(self):
        self.pending = True

    def get_draft(self, draft_id):
        return {"id": draft_id, "kind": "knowledge"}

    def apply_draft(self, draft_id, user_id):
        if not self.pending:
            return {"ok": False, "message": "이미 처리됨"}
        self.pending = False
        return {"ok": True, "op": "add", "knowledge": {"id": "KN_C001", "title": "t", "version": 1}}

    def cancel_draft(self, draft_id, user_id):
        ok, self.pending = self.pending, False
        return ok


def repos(adjust=None):
    return SimpleNamespace(coord=FakeCoord(), adjust=adjust or FakeAdjust(), knowledge=FakeKnowledge(),
                           metrics=SimpleNamespace(compare=lambda *a: {"fail_rate": 0.1}))


def actions(r):
    return Actions(SimpleNamespace(ctx=SimpleNamespace(repos=r, telegram=None, settings=SimpleNamespace(admin_ids=(1, 2)))))


def ev(type_, who, chat, **payload):
    return {"type": type_, "from_id": who, "chat_id": chat, "payload": {"message_id": 3, **payload}}


def marked(out):
    return [(m["chat_id"], m["edit_message_id"], m["buttons"]) for m in out["outbox"] if m.get("mark")]


def test_reject_marks_every_copy_and_second_press_is_ignored():
    r = repos()
    a = actions(r)
    first = a.handle(ev("action.adapt_reject", 1, 1, adj_id="A-1", name="관리자A"))
    assert marked(first) == [(1, 77, render.decided("✖ 거절", "관리자A")), (2, 78, render.decided("✖ 거절", "관리자A"))]
    assert r.adjust.status == "rejected"
    second = a.handle(ev("action.adapt_reject", 2, 2, adj_id="A-1", name="관리자B"))
    assert marked(second) == [] and "이미 처리했거나" in second["outbox"][0]["text"]
    assert "되돌릴 활성 조정이 없습니다" in a.handle(ev("action.adapt_rollback", 2, 2, adj_id="A-1"))["outbox"][0]["text"]


def test_rollback_only_once():
    r = repos(FakeAdjust("applied"))
    a = actions(r)
    assert "원래 값으로 되돌렸습니다" in a.handle(ev("action.adapt_rollback", 1, 1, adj_id="A-1"))["outbox"][0]["text"]
    assert "되돌릴 활성 조정이 없습니다" in a.handle(ev("action.adapt_rollback", 2, 2, adj_id="A-1"))["outbox"][0]["text"]


def test_approve_applies_once_and_marks_copies():
    r = repos()
    env = SimpleNamespace(ctx=SimpleNamespace(repos=r), agent={})
    out = Apply(env, retry_waits=()).apply({"payload": {"adj_id": "A-1", "name": "관리자A"}, "chat_id": 1})
    assert marked(out) == [(1, 77, render.decided("✅ 승인", "관리자A")), (2, 78, render.decided("✅ 승인", "관리자A"))]
    assert r.adjust.flags == ["llm.max_concurrency"] and out["followups"]
    r.adjust.status = "proposed"
    r.adjust.apply = lambda *a: False
    gone = Apply(env, retry_waits=()).apply({"payload": {"adj_id": "A-1", "name": "관리자B"}, "chat_id": 2})
    assert marked(gone) == [] and "이미 처리했거나" in gone["outbox"][0]["text"] and "followups" not in gone


def test_draft_save_and_cancel_mark_copies():
    r = repos()
    a = actions(r)
    saved = a.handle(ev("action.knowledge_save", 1, 1, draft_id="D-1", name="관리자A"))
    assert marked(saved)[0] == (1, 77, render.decided("✅ 저장", "관리자A"))
    assert marked(a.handle(ev("action.knowledge_cancel", 2, 2, draft_id="D-1", name="관리자B"))) == []
    r.knowledge.pending = True
    assert marked(a.handle(ev("action.knowledge_cancel", 2, 2, draft_id="D-1", name="관리자B")))[1] == (2, 78, render.decided("✖ 취소", "관리자B"))


def test_mark_edits_without_fallback_send():
    n = nodes()
    calls = []

    class Tg:
        first = 1

        def edit(self, chat, mid, text, buttons=None, html=False):
            calls.append(("edit", chat, mid, text, buttons))
            return chat == 1

        def edit_markup(self, chat, mid, buttons=None):
            calls.append(("markup", chat, mid, buttons))
            return chat == 1

        def send(self, *a, **k):
            raise AssertionError("mark must not send a new message")

    n.ctx.telegram = Tg()
    done = render.decided("✅ 승인", "관리자A")
    assert n.send(message("", chat_id=1, buttons=done, edit_message_id=77, mark=True)) == [(1, 77)]
    assert n.send(message("", chat_id=2, buttons=done, edit_message_id=78, mark=True)) == []
    assert n.send(message("목록", chat_id=1, buttons=[], edit_message_id=50, edit_text="목록", mark=True)) == [(1, 50)]
    assert [c[0] for c in calls] == ["markup", "markup", "edit"]
    s = {"run_id": "r", "event": new_event("action.adapt_reject", {}), "started_at": time.time(), "node_path": [],
         "outbox": [message("", chat_id=2, buttons=done, edit_message_id=78, mark=True)]}
    n.publish(s)
    assert n.ctx.stats["send_fail"] == 0


def test_callback_carries_presser_name():
    cb = {"update_id": 6, "callback_query": {"id": "c1", "from": {"id": 2, "first_name": "길동", "last_name": "홍"}, "data": "ar:A-1",
                                             "message": {"message_id": 4, "chat": {"id": 2}}}}
    assert to_event(cb, lambda uid: "admin")["payload"]["name"] == "길동 홍"
    cb["callback_query"]["from"] = {"id": 5}
    assert to_event(cb, lambda uid: "admin")["payload"]["name"] == "5"


class MemCoord:
    def __init__(self, links=()):
        self.links = list(links)

    def link_message(self, mid, chat, kind, ref_id):
        if not any(l[:3] == (mid, chat, kind) for l in self.links):
            self.links.append((mid, chat, kind, ref_id))

    def messages_of(self, kind, ref_id, hours=None):
        return [(chat, mid) for mid, chat, k, r in self.links if (k, r) == (kind, ref_id)]


class TwoAdmins:
    first = 1

    def broadcast(self, text, buttons=None, html=False):
        return [(1, 100), (2, 200)]

    def edit(self, chat, mid, text, buttons=None, html=False):
        return True


def published(n, s):
    n.publish({"run_id": "r", "event": new_event("alert.pipeline", {}), "started_at": time.time(), "node_path": [], **s})


def test_proposals_on_reports_and_alerts_are_marked_for_every_admin():
    n = nodes()
    n.ctx.telegram = TwoAdmins()
    prop = {"adj_id": "A-1", "paragraph": "⚙️ 제안", "buttons": [[("승인", "aa:A-1")]], "source": "diagnosis", "message_id": None,
            "message_text": None}
    for coord, s in ((MemCoord(), {"outbox": [message("리포트", ref=("incident", "I-1"))]}),
                     (MemCoord([(77, 1, "alert", "k@1"), (78, 2, "alert", "k@1")]), None)):
        n.ctx.repos.coord = coord
        if s is None:
            p = {**prop, "source": "alert", "message_id": 77, "message_text": "알림", "messages": [[1, 77], [2, 78]]}
            n.graphs["propose"] = SimpleNamespace(invoke=lambda st, p=p: {"proposal": p})
            s = {"event": {"type": "signal.policy", "payload": {"policy_key": "llm.max_concurrency", "message_id": 77, "signal": {}}},
                 "outbox": []}
        else:
            n.graphs["propose"] = SimpleNamespace(invoke=lambda st: {"proposal": prop})
            s = {"event": {"type": "alert.pipeline", "payload": {}}, "adjust_request": {"policy_key": "llm.max_concurrency"}, **s}
        published(n, n.propose({**s, "budget": None, "node_path": []}))
        copies = coord.messages_of("adjustment", "A-1")
        assert len(copies) == 2 and {c for c, _ in copies} == {1, 2}
        out = actions(SimpleNamespace(**{**repos().__dict__, "coord": coord})).handle(ev("action.adapt_reject", 1, 1, adj_id="A-1", name="관리자A"))
        assert sorted((c, m) for c, m, _ in marked(out)) == sorted(copies)
