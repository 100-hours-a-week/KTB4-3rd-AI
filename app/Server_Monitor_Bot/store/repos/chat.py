# 챗봇 대화 기억: chat_messages 원문(MySQL, mask 후 저장)과 <stage>_chat 벡터, 대화방별 압축 요약(chat_summaries).
import logging
import time

from ...hooks.mask import mask
from .base import epoch, js, unjs

log = logging.getLogger("app.Server_Monitor_Bot.repos")


class Chat:
    def __init__(self, db, vec=None, embed=None):
        self.db, self.vec, self.embed = db, vec, embed

    def recent(self, chat_id: int, turns: int = 20) -> list[dict]:
        rows = self.db.fetch_all("SELECT id, message_id, role, text, incident_id, intent, ts FROM chat_messages WHERE chat_id = %s "
                                 "ORDER BY id DESC LIMIT %s", (chat_id, turns * 2))
        return list(reversed(rows))

    def since(self, chat_id: int, after_id: int = 0) -> list[dict]:
        return self.db.fetch_all("SELECT id, message_id, role, text, incident_id, intent, ts FROM chat_messages WHERE chat_id = %s AND id > %s "
                                 "ORDER BY id", (chat_id, after_id))

    def summary_of(self, chat_id: int) -> dict:
        return self.db.fetch_one("SELECT summary, upto_id FROM chat_summaries WHERE chat_id = %s", (chat_id,)) or {"summary": "", "upto_id": 0}

    def save_summary(self, chat_id: int, summary: str, upto_id: int) -> None:
        self.db.execute("INSERT INTO chat_summaries (chat_id, summary, upto_id) VALUES (%s, %s, %s) AS new "
                        "ON DUPLICATE KEY UPDATE summary = new.summary, upto_id = new.upto_id", (chat_id, summary, upto_id))

    def incident_of_message(self, chat_id: int, message_id: int) -> str | None:
        row = self.db.fetch_one("SELECT incident_id FROM chat_messages WHERE chat_id = %s AND message_id = %s", (chat_id, message_id))
        return row["incident_id"] if row else None

    def save_turn(self, *, chat_id: int, message_id: int, reply_to: int | None, question: str, answer: str, incident_id: str | None,
                  intent: str, evidence: list) -> int:
        question, answer = mask(question) or "", mask(answer) or ""
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

    def index(self, chat_id: int, message_id: int, question: str, answer: str, incident_id: str | None, intent: str | None,
              ts: float | None = None) -> bool:
        try:
            self.vec.upsert("chat", f"chat:{chat_id}:{message_id}", self.embed.one(f"Q: {question}\nA: {answer}"),
                            {"chat_id": chat_id, "message_id": message_id, "incident_id": incident_id, "ts": int(ts or time.time()),
                             "intent": intent})
            return True
        except Exception as exc:
            log.warning("chat index failed: error=%s", type(exc).__name__)
            return False

    def search(self, query: str, incident_id: str | None = None, limit: int = 5, min_score: float = 0.0,
               vector: list[float] | None = None) -> list[dict]:
        hits = [h for h in self.vec.search("chat", vector or self.embed.one(query), limit=limit, equals={"incident_id": incident_id} if incident_id else None)
                if h["score"] >= min_score]
        if not hits:
            return []
        pairs = tuple((h["chat_id"], h["message_id"]) for h in hits)
        qs = {(r["chat_id"], r["message_id"]): r for r in self.db.fetch_all(
            "SELECT chat_id, message_id, text, ts, incident_id FROM chat_messages WHERE role = 'user' AND (chat_id, message_id) IN %s", (pairs,))}
        answers = {}
        for r in self.db.fetch_all("SELECT chat_id, reply_to_message_id, text FROM chat_messages WHERE role = 'bot' AND (chat_id, reply_to_message_id) IN %s "
                                   "ORDER BY id", (pairs,)):
            answers[(r["chat_id"], r["reply_to_message_id"])] = r["text"]
        return [{"chat_id": h["chat_id"], "message_id": h["message_id"], "question": q["text"], "answer": answers.get(k, ""), "ts": q["ts"], "incident_id": q["incident_id"],
                 "score": h["score"]} for h in hits if (q := qs.get(k := (h["chat_id"], h["message_id"])))]

    def pending(self, limit: int = 200) -> list[dict]:
        rows = self.db.fetch_all("SELECT u.chat_id, u.message_id, u.text AS question, u.incident_id, u.ts, b.text AS answer, b.intent "
                                 "FROM chat_messages u LEFT JOIN chat_messages b ON b.chat_id = u.chat_id "
                                 "AND b.reply_to_message_id = u.message_id AND b.role = 'bot' "
                                 "WHERE u.role = 'user' AND u.embed_pending = TRUE LIMIT %s", (limit,))
        return [{**r, "ts": epoch(r["ts"])} for r in rows]

    def mark_all_pending(self) -> int:
        return self.db.execute("UPDATE chat_messages SET embed_pending = TRUE WHERE role = 'user'")

    def mark_indexed(self, chat_id: int, message_id: int) -> None:
        self.db.execute("UPDATE chat_messages SET embed_pending = FALSE WHERE chat_id = %s AND message_id = %s", (chat_id, message_id))
