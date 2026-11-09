# 디스코드 채널: HTML→Markdown·특수문자 이스케이프·버튼 변환·첨부 원래 이름, REST 요청 모양, 텔레그램·디스코드 묶음 라우팅, 메시지·버튼·첨부 → to_event, 관리자 판정.
import json

import httpx

from app.Server_Monitor_Bot.channels.discord.client import LIMIT, DiscordClient, components, to_markdown
from app.Server_Monitor_Bot.channels.discord.receiver import button_update, filename, message_update
from app.Server_Monitor_Bot.channels.fanout import Channels
from app.Server_Monitor_Bot.channels.telegram.receiver import to_event
from app.Server_Monitor_Bot.config.settings import load

CHANNEL = 1500000000000000001


def client(seen: list) -> DiscordClient:
    def handle(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, json.loads(req.content or b"{}"), req.headers.get("authorization")))
        return httpx.Response(200, json={"id": "1600000000000000002"})
    return DiscordClient("tok", CHANNEL, "[LOCAL]", http=httpx.Client(transport=httpx.MockTransport(handle)))


def test_markdown_and_buttons():
    text = "<b>근거 2개</b>\n<blockquote expandable>kn:A — 가 &amp; 나\ndoc:B — 다</blockquote>\n끝"
    assert to_markdown(text, True) == "**근거 2개**\n> kn:A — 가 & 나\n> doc:B — 다\n끝"
    assert to_markdown("<b>그대로</b>", False) == "<b>그대로</b>"
    assert to_markdown("doc:02_API_계약 `a_b` *x* ~y~", False) == "doc:02\\_API\\_계약 `a_b` \\*x\\* \\~y\\~"
    rows = components([[("저장", "ks:D-1"), ("취소", "kc:D-1")], [("✅ 저장 · 관리자", "x:0"), ("✅", "x:0")]])
    assert [b["custom_id"] for b in rows[0]["components"]] == ["ks:D-1", "kc:D-1"]
    assert [b["custom_id"] for b in rows[1]["components"]] == ["x:0", "x:0~1"] and all(b["disabled"] for b in rows[1]["components"])


def test_client_requests():
    seen = []
    c = client(seen)
    assert c.send(None, "가" * 3000, [[("저장", "ks:D-1")]], reply_to=77) == 1600000000000000002
    method, path, body, auth = seen[-1]
    assert (method, path, auth) == ("POST", f"/api/v10/channels/{CHANNEL}/messages", "Bot tok")
    assert len(body["content"]) == LIMIT and body["content"].startswith("[LOCAL] ") and body["allowed_mentions"] == {"parse": []}
    assert body["message_reference"]["message_id"] == "77" and body["components"][0]["components"][0]["custom_id"] == "ks:D-1"
    assert c.broadcast("알림") == [(CHANNEL, 1600000000000000002)]
    assert c.edit_markup(CHANNEL, 5, None) and seen[-1][:3] == ("PATCH", f"/api/v10/channels/{CHANNEL}/messages/5", {"components": []})


def test_channels_route():
    calls = []

    class Fake:
        def __init__(self, name, channel_id=None):
            self.name, self.channel_id, self.token, self.first = name, channel_id, f"{name}-token", 11

        def send(self, chat, text, buttons=None, reply_to=None, html=False):
            calls.append((self.name, "send", chat))
            return 9

        def broadcast(self, text, buttons=None, html=False):
            calls.append((self.name, "broadcast", None))
            return [(self.channel_id or 11, 9)]

        def edit(self, chat, mid, text, buttons=None, html=False):
            calls.append((self.name, "edit", chat))
            return True

        def answer_callback(self, cid, text=""):
            calls.append((self.name, "answer", cid))

        def fetch_file(self, fid, max_bytes):
            calls.append((self.name, "file", fid))

    ch = Channels(Fake("tg"), Fake("dc", CHANNEL))
    ch.send(CHANNEL, "a")
    ch.send(11, "b")
    assert ch.send(None, "c") == 9 and ch.broadcast("d") == [(11, 9), (CHANNEL, 9)]
    ch.edit(CHANNEL, 1, "e")
    ch.answer_callback("dc:5")
    ch.answer_callback("777")
    ch.fetch_file("https://cdn.discordapp.com/x.md", 10)
    ch.fetch_file("BQAC", 10)
    assert ch.token == "tg-token" and ch.first == 11
    assert calls == [("dc", "send", CHANNEL), ("tg", "send", 11), ("tg", "broadcast", None), ("dc", "broadcast", None),
                     ("tg", "broadcast", None), ("dc", "broadcast", None), ("dc", "edit", CHANNEL), ("tg", "answer", "777"),
                     ("dc", "file", "https://cdn.discordapp.com/x.md"), ("tg", "file", "BQAC")]


def test_discord_updates_become_events():
    settings = load({"AI_DB_URL": "mysql://u:p@h/ai_local", "Admin_Telegram_ID": "1", "Admin_Discord_ID": "42",
                     "DISCORD_BOT_TOKEN": "t", "DISCORD_CHANNEL_ID": str(CHANNEL)})
    assert settings.discord_channel_id == CHANNEL and settings.is_admin(42) and settings.is_admin(1) and not settings.is_admin(7)
    role = lambda uid: "admin" if settings.is_admin(uid) else None
    ref = lambda chat, mid: ("alert", "k@1") if (chat, mid) == (CHANNEL, 99) else None
    status = to_event(message_update(5, CHANNEL, 42, "주인", "owner", "/status"), role, ref)
    assert (status["type"], status["chat_id"], status["from_id"]) == ("action.status", CHANNEL, 42)
    reply = to_event(message_update(6, CHANNEL, 42, "주인", "owner", "원인이 뭐야?", reply_to=99), role, ref)
    assert reply["type"] == "user.message" and reply["payload"]["reply_ref"] == "alert:k@1"
    doc = to_event(message_update(7, CHANNEL, 42, "주인", "owner", "", attachment={"url": "https://cdn/x.md", "filename": "x.md", "size": 10}),
                   role, ref)
    assert doc["type"] == "action.doc_upload" and doc["payload"]["file_id"] == "https://cdn/x.md"
    assert filename("306002dce0276e12.md", "디스코드_업로드_확인") == "디스코드_업로드_확인.md"
    assert filename("ops.md", None) == "ops.md" and filename("a.pdf", "보고서.pdf") == "보고서.pdf"
    button = to_event(button_update(8, CHANNEL, 99, 42, "주인", "ks:D-1~2"), role, ref)
    assert (button["type"], button["payload"]["draft_id"], button["payload"]["callback_id"]) == ("action.knowledge_save", "D-1", "dc:8")
    assert to_event(message_update(9, CHANNEL, 7, "남", None, "/status"), role, ref) is None
