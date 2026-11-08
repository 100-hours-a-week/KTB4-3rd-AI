# 텔레그램 팀원: /join → /request 목록·버튼 → 승인(approved·알림) / 거절(삭제·재요청), 관리자+팀원 알림 전파.
from datetime import datetime
from types import SimpleNamespace

from app.Server_Monitor_Bot.channels.telegram.client import TelegramClient
from app.Server_Monitor_Bot.config.settings import load
from app.Server_Monitor_Bot.ops_agent.actions import Actions
from app.Server_Monitor_Bot.ops_agent.roles import allowed, role_of


class FakeMembers:
    def __init__(self):
        self.rows = {}

    def request(self, user_id, chat_id, name, username):
        if user_id in self.rows:
            return self.rows[user_id]["status"]
        self.rows[user_id] = {"user_id": user_id, "chat_id": chat_id, "name": name, "username": username, "status": "pending",
                              "requested_at": datetime(2026, 10, 27, 3, 0)}
        return "new"

    def pending(self):
        return [r for r in self.rows.values() if r["status"] == "pending"]

    def get(self, user_id):
        return self.rows.get(user_id)

    def approve(self, user_id, by):
        self.rows[user_id]["status"] = "approved"
        return 1

    def reject(self, user_id):
        return 1 if self.rows.pop(user_id, None) else 0

    def is_member(self, user_id):
        return self.rows.get(user_id, {}).get("status") == "approved"

    def chats(self):
        return [r["chat_id"] for r in self.rows.values() if r["status"] == "approved"]


class FakeCoord:
    def messages_of(self, kind, ref_id, hours=None):
        return [(10, 50), (20, 60)] if (kind, ref_id, hours) == ("member", "list", 24) else []


def actions():
    settings = load({"AI_DB_URL": "mysql://u:p@h/ai_dev", "Admin_Telegram_ID": "1,2"})
    members = FakeMembers()
    repos = SimpleNamespace(members=members, coord=FakeCoord())
    return Actions(SimpleNamespace(ctx=SimpleNamespace(settings=settings, repos=repos, telegram=None))), members


def ev(type_, from_id, chat_id, **payload):
    return {"type": type_, "from_id": from_id, "chat_id": chat_id, "payload": {"message_id": 3, **payload}}


def texts(out):
    return [(m["chat_id"], m["text"]) for m in out["outbox"]]


def test_join_request_approve_reject_and_rejoin():
    a, members = actions()
    assert "가입 요청을 보냈습니다" in texts(a.handle(ev("action.member_join", 7, 70, name="홍길동", username="hong")))[0][1]
    assert "이미 승인 대기 중" in texts(a.handle(ev("action.member_join", 7, 70)))[0][1]
    assert "이미 등록된 사용자" in texts(a.handle(ev("action.member_join", 1, 10)))[0][1]
    assert "이미 등록된 사용자" in texts(a.handle(ev("action.member_join", 2, 20)))[0][1]
    a.handle(ev("action.member_join", 8, 80, name="김철수"))
    listed = a.handle(ev("action.member_list", 1, 10))["outbox"][0]
    assert "가입 요청 2건" in listed["text"] and "user 7 · chat 70" in listed["text"] and "12:00" in listed["text"]
    assert listed["buttons"][0] == [("승인 · 홍길동", "ma:7"), ("거절", "mr:7")] and listed["ref"] == ("member", "list")
    out = a.handle(ev("action.member_approve", 1, 10, user_id="7", name="관리자A"))["outbox"]
    refreshed = [m for m in out if m["mark"]]
    assert [(m["chat_id"], m["edit_message_id"]) for m in refreshed] == [(10, 50), (20, 60)]
    assert all("가입 요청 1건" in m["edit_text"] and "처리: 홍길동 승인 · 관리자A" in m["edit_text"] for m in refreshed)
    assert refreshed[0]["buttons"] == [[("승인 · 김철수", "ma:8"), ("거절", "mr:8")]]
    assert texts({"outbox": [m for m in out if not m["mark"]]}) == [(10, "✅ 승인했습니다: 홍길동"),
                                                                    (70, "✅ 가입이 승인되었습니다. /help로 명령을 확인하세요")]
    assert members.is_member(7) and "이미 처리된 요청" in texts(a.handle(ev("action.member_approve", 2, 20, user_id="7")))[0][1]
    out = a.handle(ev("action.member_reject", 2, 20, user_id="8", name="관리자B"))["outbox"]
    assert out[0]["edit_text"].startswith("대기 중인 가입 요청이 없습니다") and "처리: 김철수 거절 · 관리자B" in out[0]["edit_text"]
    assert out[0]["buttons"] == []
    assert texts({"outbox": out})[-1] == (80, "가입이 거절되었습니다. 다시 요청하려면 /join") and members.get(8) is None
    assert "가입 요청을 보냈습니다" in texts(a.handle(ev("action.member_join", 8, 80)))[0][1]
    a.handle(ev("action.member_reject", 1, 10, user_id="8"))
    assert "대기 중인 가입 요청이 없습니다" in texts(a.handle(ev("action.member_list", 1, 10)))[0][1]


def test_roles():
    settings = load({"AI_DB_URL": "mysql://u:p@h/ai_dev", "Admin_Telegram_ID": "1, 5"})
    members = FakeMembers()
    members.rows[2] = {"status": "approved", "chat_id": 20}
    members.rows[3] = {"status": "pending", "chat_id": 30}
    assert [role_of(u, settings, members) for u in (1, 5, 2, 3, 4, None)] == ["admin", "admin", "member", None, None, None]
    assert allowed("member", "user.message") and not allowed("member", "action.member_approve") and not allowed("member", "action.drill_guess")
    assert allowed(None, "action.member_join") and not allowed(None, "action.status") and allowed("admin", "action.member_list")
    broken = SimpleNamespace(is_member=lambda uid: 1 / 0)
    assert role_of(2, settings, broken) is None
    assert load({"Admin_Telegram_ID": "abc"}).admin_ids == () and load({}).admin_ids == ()
    assert load({"Admin_Telegram_ID": " 123 , abc,-456,123,"}).admin_ids == (123, -456)


def test_broadcast_reaches_admin_and_members():
    calls = []

    class Http:
        def post(self, url, json, timeout):
            calls.append(json["chat_id"])
            return SimpleNamespace(status_code=200, json=lambda: {"ok": True, "result": {"message_id": 100 + len(calls)}})

    tg = TelegramClient("t", (10,), http=Http(), recipients=lambda: [20, 10, 30])
    assert tg.broadcast("알림") == [(10, 101), (20, 102), (30, 103)]
    assert tg.send(None, "알림") == 104 and calls[3:] == [10, 20, 30]
    assert tg.send(55, "답") == 107 and calls[-1] == 55
    failing = TelegramClient("t", (10,), http=Http(), recipients=lambda: 1 / 0)
    assert failing.broadcast("x") == [(10, 108)]
    two = TelegramClient("t", (10, 11), http=Http(), recipients=lambda: [11, 20])
    assert two.targets() == [10, 11, 20] and two.first == 10
