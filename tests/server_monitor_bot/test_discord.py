# 디스코드 채널: HTML→Markdown·특수문자 이스케이프·버튼 변환·첨부 원래 이름, 알림·답변 서식과 2,000자 자르기, REST 요청 모양, 텔레그램·디스코드 묶음 라우팅, 메시지·버튼·첨부 → to_event, 관리자 판정.
import json

import httpx

from app.Server_Monitor_Bot.channels.discord.client import LIMIT, OMITTED, REPLY_LINE, DiscordClient, components, to_markdown
from app.Server_Monitor_Bot.channels.telegram import render as r
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
    text = "<b>근거 2개</b>\n<blockquote expandable>kn:A@v1 — 가 &amp; 나\ndoc:B#2, metric:r.s@14:33 — 다\n…</blockquote>\n<b>끝</b>"
    assert to_markdown(text, True) == "-# 근거 A · B #2 · metric:r.s\n**끝**"
    chats = "<b>근거 3개</b>\n<blockquote expandable>chat:1,chat:2 — q\nkn:A@v1 — a\n지난 대화 · 3 — q2\nkn:A@v1, req:x — b</blockquote>"
    assert to_markdown(chats, True) == "-# 근거 A · req:x · 지난 대화 3건"
    assert to_markdown("<b>근거 0개</b>\n<blockquote expandable></blockquote>\n본문", True) == "\n본문"
    assert to_markdown("<b>그대로</b>", False) == "<b>그대로</b>"
    assert to_markdown("doc:02_API_계약 `a_b` *x* ~y~", False) == "doc:02\\_API\\_계약 `a_b` \\*x\\* \\~y\\~"
    rows = components([[("저장", "ks:D-1"), ("취소", "kc:D-1")], [("✅ 저장 · 관리자", "x:0"), ("✅", "x:0")]])
    assert [b["custom_id"] for b in rows[0]["components"]] == ["ks:D-1", "kc:D-1"]
    assert [b["custom_id"] for b in rows[1]["components"]] == ["x:0", "x:0~1"] and all(b["disabled"] for b in rows[1]["components"])


def test_layout_and_limit():
    c = DiscordClient(None, CHANNEL, "[DEV]")
    sig = {"feature": "receipt", "step_id": "image_fetch", "reason": "http_4xx", "status": 404, "fails": 2, "total": 2}
    kn = {"id": "KN_IMAGE_NOT_FOUND", "version": 1, "title": "이미지 객체 없음(404)", "severity": "medium", "cause": "객체 없음",
          "resolution": "키 확인", "checks": ["image_host"]}
    alert = r.alert_known({"signal": sig, "at": 1793079180.0, "samples": ["a_1"]}, kn)
    assert c.text(alert) == (
        "[DEV] **🟠 영수증 · 이미지 다운로드 실패**\n파일 없음(404) 2/2건 · 최근 5분 · 14:33\n"
        "-# 코드: image\\_fetch · POST /receipt → IMAGE\\_FETCH\\_FAILED · app/features/receipt/image.py · download()\n\n"
        "**원인** 객체 없음\n**조치** 키 확인\n**확인할 곳** image\\_host\n"
        "-# 알려진 장애: 이미지 객체 없음(404) · KN\\_IMAGE\\_NOT\\_FOUND v1\n-# 샘플: /trace a\\_1\n\n-# ↩ 답장하면 이 장애로 대화합니다")
    assert c.text(f"[DEV] {alert}") == c.text(alert)
    answer, is_html = r.chat_answer("원인은 만료입니다.\n원인이 뭐냐면: 아님", [{"id": "kn:KN_A@v1", "summary": "s", "data": "{}"}], ["kn:KN_A@v1"])
    assert c.text(answer, is_html) == "[DEV] 원인은 만료입니다.\n원인이 뭐냐면: 아님\n-# 근거 KN\\_A"
    report = {"summary": "s", "cause_text": "c", "resolution_text": "r", "step_id": "luna_call", "confidence": "low", "unknown": False}
    diag = c.text(r.diagnosis(report, [], "receipt"), True)
    assert "\n**원인(추정) · 확신도 낮음** c\n**조치** r\n-# 예상 문제: 근거가 없어 모릅니다\n" in diag
    daily = c.text(r.daily("d", {"llm": [], "fail_reasons": [], "monitor": None, "health": None}))
    assert daily.startswith("[DEV] **📊 일일 리포트 · d**\n\n**LLM 호출**\n· 호출 없음")
    evidence = [{"id": f"req:{i:03d}", "summary": "x", "data": "{}"} for i in range(30)]
    long = {**report, "summary": "가" * 1700, "evidence_ids": [e["id"] for e in evidence]}
    assert "-# 근거 req:000" in c.text(r.diagnosis({**long, "summary": "가"}, evidence, "receipt"), True)
    fit = c.text(r.diagnosis(long, evidence, "receipt"), True)
    assert len(fit) <= LIMIT and "-# 근거" not in fit and "**조치** r" in fit and fit.endswith("대화합니다")
    over = c.text(r.diagnosis({**long, "summary": "가" * 1800 + "\n" + "나" * 300}, evidence, "receipt"), True)
    assert len(over) <= LIMIT and over.endswith(f"가\n{OMITTED}\n{REPLY_LINE}") and "나" not in over and over.count(REPLY_LINE) == 1
    lines = ("줄" * 300 + "\n") * 10
    cut = c.text(lines)
    assert len(cut) <= LIMIT and cut.endswith(f"줄\n{OMITTED}") and REPLY_LINE not in cut
    assert all(len(x) == 300 for x in cut.removeprefix("[DEV] ").split("\n")[:-1])
    alert = c.text(r.alert_known({"signal": sig, "at": 1793079180.0, "samples": []}, {**kn, "cause": "원인 " * 700}))
    assert len(alert) <= LIMIT and alert.startswith("[DEV] **🟠 영수증") and alert.endswith(f"{OMITTED}\n{REPLY_LINE}")
    tail = c.text("a\n" * 5 + "나" * 3000)
    assert len(tail) <= LIMIT and tail.count("나") > 1900 and tail.endswith(f"나\n{OMITTED}")
    bold = c.text("🟠 제목 " + "가" * 3000)
    assert len(bold) <= LIMIT and bold.startswith("[DEV] **🟠 제목") and bold.endswith(f"가**\n{OMITTED}")
    under, odd = c.text("a_" * 1500), c.text("z" + "a_" * 1500)
    assert len(under) <= LIMIT and under.endswith(f"\n{OMITTED}") and "\\\n" not in under and "\\\n" not in odd
    path = c.text("경로 " + "\\" * 3000)
    assert len(path) <= LIMIT and path.count("\\") > 1900 and path.count("\\") % 2 == 0


def test_client_requests():
    seen = []
    c = client(seen)
    assert c.send(None, "가" * 3000, [[("저장", "ks:D-1")]], reply_to=77) == 1600000000000000002
    method, path, body, auth = seen[-1]
    assert (method, path, auth) == ("POST", f"/api/v10/channels/{CHANNEL}/messages", "Bot tok")
    assert LIMIT - 3 <= len(body["content"]) <= LIMIT and body["content"].startswith("[LOCAL] ") and body["allowed_mentions"] == {"parse": []}
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
