# 텔레그램 Bot API 클라이언트(httpx 동기). 머리 [STAGE]를 붙이고 10초·3회(1·3·9초) 다시 시도한다. html이면 parse_mode HTML, 텔레그램이 400으로 거부하면 일반 텍스트로 한 번 더. 파일 받기는 URL을 로그에 남기지 않는다.
import logging
import time

import httpx

from .render import plain

log = logging.getLogger("app.Server_Monitor_Bot.telegram")
API = "https://api.telegram.org/bot{token}/{method}"
FILE = "https://api.telegram.org/file/bot{token}/{path}"
WAITS = (1, 3, 9)
LIMIT = 4096


def markup(buttons) -> dict | None:
    if not buttons:
        return None
    return {"inline_keyboard": [[{"text": label, "callback_data": data[:64]} for label, data in row] for row in buttons]}


class TelegramClient:
    def __init__(self, token: str | None, admins: tuple = (), head: str = "", stats: dict | None = None,
                 http: httpx.Client | None = None, recipients=lambda: []):
        self.token, self.admins, self.head, self.recipients = token, tuple(admins), head, recipients
        self.last_status = None
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
                self.last_status = res.status_code
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

    def text(self, text: str, html: bool = False) -> str:
        full = f"{self.head} {text}" if self.head and not text.startswith(self.head) else text
        return full if html else full[:LIMIT]

    @property
    def first(self) -> int | None:
        return self.admins[0] if self.admins else None

    def targets(self) -> list[int]:
        try:
            extra = list(self.recipients())
        except Exception as exc:
            log.warning("telegram recipients failed: error=%s", type(exc).__name__)
            extra = []
        return list(dict.fromkeys(c for c in [*self.admins, *extra] if c))

    def broadcast(self, text: str, buttons=None, html: bool = False) -> list[tuple[int, int]]:
        sent = [(chat, self.send(chat, text, buttons, html=html)) for chat in self.targets()]
        return [(chat, mid) for chat, mid in sent if mid]

    def send(self, chat_id: int | None, text: str, buttons=None, reply_to: int | None = None, html: bool = False) -> int | None:
        if chat_id is None:
            sent = self.broadcast(text, buttons, html)
            return sent[0][1] if sent else None
        payload = {"chat_id": chat_id, "text": self.text(text, html)}
        if html:
            payload["parse_mode"] = "HTML"
        if buttons:
            payload["reply_markup"] = markup(buttons)
        if reply_to:
            payload["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        self.last_status = None
        body = self.call("sendMessage", payload)
        if body is None and html and self.last_status == 400:
            return self.send(chat_id, plain(text), buttons, reply_to)
        if body is None:
            self.stats["send_fail"] = self.stats.get("send_fail", 0) + 1
            return None
        return body["result"]["message_id"]

    def edit(self, chat_id: int | None, message_id: int, text: str, buttons=None, html: bool = False) -> bool:
        payload = {"chat_id": chat_id or self.first, "message_id": message_id, "text": self.text(text, html)}
        if html:
            payload["parse_mode"] = "HTML"
        if buttons:
            payload["reply_markup"] = markup(buttons)
        return self.call("editMessageText", payload, retry=False) is not None

    def edit_markup(self, chat_id: int, message_id: int, buttons=None) -> bool:
        payload = {"chat_id": chat_id, "message_id": message_id, "reply_markup": markup(buttons) or {"inline_keyboard": []}}
        return self.call("editMessageReplyMarkup", payload, retry=False) is not None

    def fetch_file(self, file_id: str | None, max_bytes: int) -> bytes | None:
        body = self.call("getFile", {"file_id": file_id}, retry=False) if file_id else None
        path = ((body or {}).get("result") or {}).get("file_path")
        if not path:
            return None
        try:
            with self.http.stream("GET", FILE.format(token=self.token, path=path), timeout=60) as res:
                if res.status_code != 200:
                    log.warning("telegram file failed: status=%s", res.status_code)
                    return None
                data = b""
                for part in res.iter_bytes():
                    data += part
                    if len(data) > max_bytes:
                        log.warning("telegram file too large: limit=%s", max_bytes)
                        return None
                return data
        except httpx.HTTPError as exc:
            log.warning("telegram file failed: error=%s", type(exc).__name__)
            return None

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]}, retry=False)

    def get_updates(self, offset: int, timeout: int = 30) -> list[dict] | None:
        body = self.call("getUpdates", {"offset": offset, "timeout": timeout, "allowed_updates": ["message", "callback_query"]},
                         timeout=timeout + 10, retry=False)
        return body["result"] if body else None
