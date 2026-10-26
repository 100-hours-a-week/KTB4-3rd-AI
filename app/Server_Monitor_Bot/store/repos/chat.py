# 챗봇 대화 기억: chat_messages 원문(MySQL)과 <stage>_chat 벡터.
import logging
import time

from .base import js, unjs

log = logging.getLogger("app.Server_Monitor_Bot.repos")


class Chat:
    def __init__(self, db, vec=None, embed=None):
        self.db, self.vec, self.embed = db, vec, embed

    def recent(self, chat_id: int, turns: int = 20) -> list[dict]:
        rows = self.db.fetch_all("SELECT id, message_id, role, text, incident_id, intent, ts FROM chat_messages WHERE chat_id = %s "
                                 "ORDER BY id DESC LIMIT %s", (chat_id, turns * 2))
        return list(reversed(rows))

    def incident_of_message(self, chat_id: int, message_id: int) -> str | None:
        row = self.db.fetch_one("SELECT incident_id FROM chat_messages WHERE chat_id = %s AND message_id = %s", (chat_id, message_id))
        return row["incident_id"] if row else None

    def save_turn(self, *, chat_id: int, message_id: int, reply_to: int | None, question: str, answer: str, incident_id: str | None,
                  intent: str, evidence: list) -> int:
        with self.db.transaction() as cur:
            cur.execute("INSERT INTO chat_messages (chat_id, message_id, reply_to_message_id, role, text, incident_id, ts) "
                        "VALUES (%s, %s, %s, 'user', %s, %s, NOW(3)) AS new ON DUPLICATE KEY UPDATE text = new.text",
                        (chat_id, message_id, reply_to, question, incident_id))
            cur.execute("INSERT INTO chat_messages (chat_id, message_id, reply_to_message_id, role, text, incident_id, intent, evidence, ts) "
                        "VALUES (%s, NULL, %s, 'bot', %s, %s, %s, %s, NOW(3))", (chat_id, message_id, answer, incident_id, intent, js(evidence)))
            bot_row = cur.lastrowid
        ok = self.index(chat_id, message_id, question, answer, incident_id, intent)
        if not ok:
            self.db.execute("UPDATE chat_messages SET embed_pending = TRUE WHERE chat_id = %s AND message_id = %s", (chat_id, message_id))
        return bot_row

    def fill_bot_message(self, row_id: int, message_id: int) -> None:
        self.db.execute("UPDATE chat_messages SET message_id = %s WHERE id = %s AND message_id IS NULL", (message_id, row_id))

    def index(self, chat_id: int, message_id: int, question: str, answer: str, incident_id: str | None, intent: str | None) -> bool:
        try:
            self.vec.upsert("chat", f"chat:{chat_id}:{message_id}", self.embed.one(f"Q: {question}\nA: {answer}"),
                            {"chat_id": chat_id, "message_id": message_id, "incident_id": incident_id, "ts": int(time.time()), "intent": intent})
            return True
        except Exception as exc:
            log.warning("chat index failed: error=%s", type(exc).__name__)
            return False

    def search(self, query: str, incident_id: str | None = None, limit: int = 5, min_score: float = 0.0) -> list[dict]:
        hits = self.vec.search("chat", self.embed.one(query), limit=limit, equals={"incident_id": incident_id} if incident_id else None)
        out = []
        for h in hits:
            if h["score"] < min_score:
                continue
            q = self.db.fetch_one("SELECT text, ts, incident_id FROM chat_messages WHERE chat_id = %s AND message_id = %s AND role = 'user'",
                                  (h["chat_id"], h["message_id"]))
            a = self.db.fetch_one("SELECT text FROM chat_messages WHERE chat_id = %s AND reply_to_message_id = %s AND role = 'bot' "
                                  "ORDER BY id DESC LIMIT 1", (h["chat_id"], h["message_id"]))
            if q:
                out.append({"message_id": h["message_id"], "question": q["text"], "answer": a["text"] if a else "",
                            "ts": q["ts"], "incident_id": q["incident_id"], "score": h["score"]})
        return out

    def pending(self, limit: int = 200) -> list[dict]:
        return self.db.fetch_all("SELECT u.chat_id, u.message_id, u.text AS question, u.incident_id, b.text AS answer, b.intent FROM chat_messages u "
                                 "LEFT JOIN chat_messages b ON b.chat_id = u.chat_id AND b.reply_to_message_id = u.message_id AND b.role = 'bot' "
                                 "WHERE u.role = 'user' AND u.embed_pending = TRUE LIMIT %s", (limit,))

    def mark_indexed(self, chat_id: int, message_id: int) -> None:
        self.db.execute("UPDATE chat_messages SET embed_pending = FALSE WHERE chat_id = %s AND message_id = %s", (chat_id, message_id))
