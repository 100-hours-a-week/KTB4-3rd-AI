# 디스코드 봇 REST 클라이언트(httpx 동기, API v10): 텔레그램 클라이언트와 같은 메서드로 채널 하나에 보내고 고치고 첨부를 받는다. HTML은 Markdown으로 바꾸고 2,000자로 자른다.
import html
import logging
import re
import time

import httpx

log = logging.getLogger("app.Server_Monitor_Bot.discord")
API = "https://discord.com/api/v10"
LIMIT = 2000
WAITS = (1, 3, 9)
MARKS = re.compile(r"([\\*_~|])")


def escape(text: str) -> str:
    return "".join(part if part.startswith("`") else MARKS.sub(r"\\\1", part) for part in re.split(r"(`[^`\n]+`)", text))


def to_markdown(text: str, is_html: bool) -> str:
    text = escape(text)
    if not is_html:
        return text
    text = re.sub(r"<blockquote[^>]*>(.*?)</blockquote>", lambda m: "\n".join(f"> {line}" for line in m.group(1).split("\n")), text, flags=re.S)
    text = re.sub(r"</?b>", "**", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


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
        full = f"{self.head} {text}" if self.head and not text.startswith(self.head) else text
        return to_markdown(full, html)[:LIMIT]

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
