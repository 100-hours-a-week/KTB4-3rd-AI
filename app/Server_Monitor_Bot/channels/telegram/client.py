# 텔레그램 Bot API 클라이언트(httpx 동기). 머리 [stage·task6]를 붙이고 10초·3회(1·3·9초) 다시 시도한다.
import logging
import time

import httpx

log = logging.getLogger("app.Server_Monitor_Bot.telegram")
API = "https://api.telegram.org/bot{token}/{method}"
WAITS = (1, 3, 9)
LIMIT = 4096


def markup(buttons) -> dict | None:
    if not buttons:
        return None
    return {"inline_keyboard": [[{"text": label, "callback_data": data[:64]} for label, data in row] for row in buttons]}


class TelegramClient:
    def __init__(self, token: str | None, default_chat: int | None, head: str = "", stats: dict | None = None,
                 http: httpx.Client | None = None):
        self.token, self.default_chat, self.head = token, default_chat, head
        self.stats = stats if stats is not None else {"send_fail": 0}
        self.http = http or httpx.Client(timeout=10)

    def call(self, method: str, payload: dict, timeout: float = 10, retry: bool = True) -> dict | None:
        if not self.token:
            return None
        for attempt, wait in enumerate((0, *WAITS) if retry else (0,)):
            if wait:
                time.sleep(wait)
            try:
                res = self.http.post(API.format(token=self.token, method=method), json=payload, timeout=timeout)
                body = res.json()
                if res.status_code == 429:
                    time.sleep(min(float(body.get("parameters", {}).get("retry_after", 1)), 30))
                    continue
                if res.status_code >= 500:
                    continue
                if not body.get("ok"):
                    log.warning("telegram call rejected: method=%s status=%s", method, res.status_code)
                    return None
                return body
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("telegram call failed: method=%s error=%s attempt=%s", method, type(exc).__name__, attempt + 1)
        return None

    def text(self, text: str) -> str:
        full = f"{self.head} {text}" if self.head and not text.startswith(self.head) else text
        return full[:LIMIT]

    def send(self, chat_id: int | None, text: str, buttons=None, reply_to: int | None = None) -> int | None:
        payload = {"chat_id": chat_id or self.default_chat, "text": self.text(text)}
        if not payload["chat_id"]:
            return None
        if buttons:
            payload["reply_markup"] = markup(buttons)
        if reply_to:
            payload["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        body = self.call("sendMessage", payload)
        if body is None:
            self.stats["send_fail"] = self.stats.get("send_fail", 0) + 1
            return None
        return body["result"]["message_id"]

    def edit(self, chat_id: int | None, message_id: int, text: str, buttons=None) -> bool:
        payload = {"chat_id": chat_id or self.default_chat, "message_id": message_id, "text": self.text(text)}
        if buttons:
            payload["reply_markup"] = markup(buttons)
        return self.call("editMessageText", payload, retry=False) is not None

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]}, retry=False)

    def get_updates(self, offset: int, timeout: int = 30) -> list[dict]:
        body = self.call("getUpdates", {"offset": offset, "timeout": timeout, "allowed_updates": ["message", "callback_query"]},
                         timeout=timeout + 10, retry=False)
        return body["result"] if body else []
