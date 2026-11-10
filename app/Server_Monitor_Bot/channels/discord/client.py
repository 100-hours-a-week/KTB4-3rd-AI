# 디스코드 봇 REST 클라이언트(httpx 동기, API v10): 텔레그램 클라이언트와 같은 메서드로 채널 하나에 보내고 고치고 첨부를 받는다. 제목·이름표는 굵게, 부가 정보와 접힌 근거는 작은 글씨 한 줄로 바꾸고, 2,000자를 넘으면 근거 줄부터 빼고 줄 경계(앞쪽뿐이면 줄 중간)에서 자른 뒤 생략 안내와, 원문에 있던 답장 안내를 붙인다.
import html
import logging
import re
import time

import httpx

from ..telegram.render import HEADINGS, ICONS, LABELS, META, REPLY

log = logging.getLogger("app.Server_Monitor_Bot.discord")
API = "https://discord.com/api/v10"
LIMIT = 2000
OMITTED = "-# … 2,000자를 넘어 일부를 생략했습니다"
REPLY_LINE = f"-# {REPLY}"
WAITS = (1, 3, 9)
MARKS = re.compile(r"([\\*_~|])")


def escape(text: str) -> str:
    return "".join(part if part.startswith("`") else MARKS.sub(r"\\\1", part) for part in re.split(r"(`[^`\n]+`)", text))


def ref(item: str) -> str:
    kind, _, rest = item.partition(":")
    rest = rest.split("@")[0]
    return rest if kind == "kn" else rest.replace("#", " #") if kind == "doc" else item.split("@")[0]


def refs(fold: str) -> str:
    found, chats = [], 0
    for line in fold.split("\n"):
        head = line.split(" — ")[0].strip()
        if head.startswith("지난 대화"):
            chats += 1
            continue
        for item in (x.strip() for x in head.split(",")):
            if item.startswith("chat:"):
                chats += 1
            elif item and item != "…" and ref(item) not in found:
                found.append(ref(item))
    found += [f"지난 대화 {chats}건"] if chats else []
    return f"-# 근거 {' · '.join(found)}" if found else ""


def to_markdown(text: str, is_html: bool) -> str:
    text = escape(text)
    if not is_html:
        return text
    text = re.sub(r"(?:<b>[^<\n]*</b>\n)?<blockquote[^>]*>(.*?)</blockquote>", lambda m: refs(m.group(1)), text, flags=re.S)
    text = re.sub(r"</?b>", "**", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def style(text: str) -> str:
    out = []
    for i, line in enumerate(text.split("\n")):
        name, sep, rest = line.partition(": ")
        if line.startswith("-# "):
            pass
        elif i == 0 and line.startswith(ICONS) or line in HEADINGS:
            line = f"**{line}**"
        elif line.startswith(META):
            line = f"-# {line}"
        elif sep and name.split(" · ")[0] in LABELS:
            line = f"**{name}** {rest}"
        out.append(line)
    return "\n".join(out)


def trim(text: str, limit: int) -> str:
    if len(text) > limit:
        text = "\n".join(x for x in text.split("\n") if not x.startswith("-# 근거 "))
    if len(text) <= limit:
        return text
    lines = text.split("\n")
    end = "\n".join([OMITTED, *([REPLY_LINE] if REPLY_LINE in lines else [])])
    body = "\n".join(x for x in lines if x != REPLY_LINE)[: limit - len(end) - 3]
    cut = body.rfind("\n")
    if cut > len(body) // 2:
        return f"{body[:cut]}\n{end}"
    body = body[:-1] if (len(body) - len(body.rstrip("\\"))) % 2 else body
    return f"{body}{'**' if body.rsplit(chr(10), 1)[-1].count('**') % 2 else ''}\n{end}"


def components(buttons) -> list[dict]:
    rows, seen = [], set()
    for row in (buttons or [])[:5]:
        items = []
        for label, data in row[:5]:
            cid, n = data[:100], 1
            while cid in seen:
                cid, n = f"{data[:94]}~{n}", n + 1
            seen.add(cid)
            done = data.startswith("x:")
            items.append({"type": 2, "style": 2 if done else 1, "label": label[:80], "custom_id": cid, "disabled": done})
        rows.append({"type": 1, "components": items})
    return rows


class DiscordClient:
    def __init__(self, token: str | None, channel_id: int | None, head: str = "", stats: dict | None = None, http: httpx.Client | None = None):
        self.token, self.channel_id, self.head = token, channel_id, head
        self.stats = stats if stats is not None else {"send_fail": 0}
        self.http = http or httpx.Client(timeout=10)

    def call(self, method: str, path: str, payload: dict | None = None) -> dict | None:
        if not self.token:
            return None
        for attempt, wait in enumerate((0, *WAITS)):
            if wait:
                time.sleep(wait)
            try:
                res = self.http.request(method, f"{API}{path}", json=payload, headers={"Authorization": f"Bot {self.token}"})
                if res.status_code == 429:
                    time.sleep(min(float(res.json().get("retry_after", 1)), 30))
                    continue
                if res.status_code >= 500:
                    continue
                if res.status_code >= 400:
                    log.warning("discord call rejected: method=%s status=%s", method, res.status_code)
                    return None
                return res.json() if res.content else {}
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("discord call failed: method=%s error=%s attempt=%s", method, type(exc).__name__, attempt + 1)
        return None

    def text(self, text: str, html: bool = False) -> str:
        if self.head and text.startswith(self.head):
            text = text[len(self.head):].lstrip(" ")
        body = style(to_markdown(text, html))
        return f"{self.head} {trim(body, LIMIT - len(self.head) - 1)}" if self.head else trim(body, LIMIT)

    def send(self, chat_id: int | None, text: str, buttons=None, reply_to: int | None = None, html: bool = False) -> int | None:
        channel = chat_id or self.channel_id
        payload = {"content": self.text(text, html), "components": components(buttons), "allowed_mentions": {"parse": []}}
        if reply_to:
            payload["message_reference"] = {"message_id": str(reply_to), "fail_if_not_exists": False}
        body = self.call("POST", f"/channels/{channel}/messages", payload)
        if body is None:
            self.stats["send_fail"] = self.stats.get("send_fail", 0) + 1
            return None
        return int(body["id"])

    def broadcast(self, text: str, buttons=None, html: bool = False) -> list[tuple[int, int]]:
        mid = self.send(self.channel_id, text, buttons, html=html)
        return [(self.channel_id, mid)] if mid else []

    def edit(self, chat_id: int | None, message_id: int, text: str, buttons=None, html: bool = False) -> bool:
        payload = {"content": self.text(text, html), "components": components(buttons)}
        return self.call("PATCH", f"/channels/{chat_id or self.channel_id}/messages/{message_id}", payload) is not None

    def edit_markup(self, chat_id: int, message_id: int, buttons=None) -> bool:
        return self.call("PATCH", f"/channels/{chat_id}/messages/{message_id}", {"components": components(buttons)}) is not None

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        return None

    def fetch_file(self, url: str | None, max_bytes: int) -> bytes | None:
        if not url:
            return None
        try:
            with self.http.stream("GET", url, timeout=60) as res:
                if res.status_code != 200:
                    log.warning("discord file failed: status=%s", res.status_code)
                    return None
                data = b""
                for part in res.iter_bytes():
                    data += part
                    if len(data) > max_bytes:
                        log.warning("discord file too large: limit=%s", max_bytes)
                        return None
                return data
        except httpx.HTTPError as exc:
            log.warning("discord file failed: error=%s", type(exc).__name__)
            return None
