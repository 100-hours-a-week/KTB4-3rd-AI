# P7 텔레그램 수신(리더만): 허용 사용자의 메시지·답장·명령·버튼을 이벤트로 바꿔 큐에 넣는다.
import logging
import threading
import time

from ...runtime.context import new_event

log = logging.getLogger("app.Server_Monitor_Bot.receiver")
COMMANDS = {"/status": "action.status", "/flags": "action.flags", "/knowledge": "action.knowledge_list", "/help": "action.help"}
DRILL = {"start": "action.drill_start", "stop": "action.drill_stop", "guess": "action.drill_guess"}
CALLBACKS = {"ks": "action.knowledge_save", "kc": "action.knowledge_cancel", "aa": "adapt.approved",
             "ar": "action.adapt_reject", "ab": "action.adapt_rollback"}


def role_of(user_id: int | None, telegram_cfg: dict) -> str | None:
    if user_id in telegram_cfg.get("owner_ids", []):
        return "owner"
    if user_id in telegram_cfg.get("drill_operator_ids", []):
        return "drill_operator"
    return None


def to_event(update: dict, telegram_cfg: dict, ref_of=lambda chat_id, message_id: None) -> dict | None:
    if "callback_query" in update:
        cb = update["callback_query"]
        user, chat = cb.get("from", {}).get("id"), cb.get("message", {}).get("chat", {}).get("id")
        if not role_of(user, telegram_cfg):
            return None
        action, _, target = (cb.get("data") or "").partition(":")
        if action not in CALLBACKS or not target:
            return None
        key = "adj_id" if action in ("aa", "ar", "ab") else "draft_id"
        return new_event(CALLBACKS[action], {key: target, "callback_id": cb.get("id"), "message_id": cb.get("message", {}).get("message_id")},
                         source="telegram", from_id=user, chat_id=chat, event_id=f"tg-cb-{cb.get('id')}")
    msg = update.get("message") or {}
    user, chat, text = msg.get("from", {}).get("id"), msg.get("chat", {}).get("id"), (msg.get("text") or "").strip()
    if not text or not role_of(user, telegram_cfg):
        return None
    eid = f"tg-{chat}-{msg.get('message_id')}"
    base = {"message_id": msg.get("message_id")}
    if text.startswith("/"):
        cmd, _, rest = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        rest = rest.strip()
        if cmd == "/trace":
            return new_event("user.trace", {**base, "request_id": rest, "text": text}, source="telegram", from_id=user, chat_id=chat, event_id=eid)
        if cmd == "/drill":
            sub, _, arg = rest.partition(" ")
            if sub not in DRILL:
                return new_event("action.help", base, source="telegram", from_id=user, chat_id=chat, event_id=eid)
            return new_event(DRILL[sub], {**base, "arg": arg.strip()}, source="telegram", from_id=user, chat_id=chat, event_id=eid)
        if cmd in COMMANDS:
            return new_event(COMMANDS[cmd], {**base, "query": rest}, source="telegram", from_id=user, chat_id=chat, event_id=eid)
        return new_event("action.help", base, source="telegram", from_id=user, chat_id=chat, event_id=eid)
    reply_to = (msg.get("reply_to_message") or {}).get("message_id")
    ref = ref_of(chat, reply_to) if reply_to else None
    return new_event("user.message", {**base, "text": text[:4000], "reply_to_message_id": reply_to,
                                      "reply_ref": f"{ref[0]}:{ref[1]}" if ref else None},
                     source="telegram", from_id=user, chat_id=chat, event_id=eid)


class Receiver(threading.Thread):
    def __init__(self, ctx, stop_event: threading.Event):
        super().__init__(name="server-bot-p7", daemon=True)
        self.ctx, self.stop_event = ctx, stop_event

    def run(self) -> None:
        cfg = self.ctx.settings.section("telegram")
        coord = self.ctx.repos.coord
        while not self.stop_event.is_set():
            if not self.ctx.is_leader() or not self.ctx.telegram or not self.ctx.telegram.token:
                self.stop_event.wait(5)
                continue
            try:
                offset = coord.tg_offset(self.ctx.stage)
                updates = self.ctx.telegram.get_updates(offset + 1 if offset else 0, cfg.get("poll_timeout_s", 30))
                for update in updates:
                    event = to_event(update, cfg, coord.ref_of)
                    if event:
                        self.ctx.enqueue(event, None)
                    coord.set_tg_offset(self.ctx.stage, update["update_id"])
            except Exception as exc:
                log.warning("receiver failed: error=%s", type(exc).__name__)
                time.sleep(5)
