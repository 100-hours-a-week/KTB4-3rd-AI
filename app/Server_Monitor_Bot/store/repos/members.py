# 텔레그램 팀원: /join 요청(pending) → 관리자 승인(approved) 또는 거절(행 삭제, 다시 요청 가능).
class Members:
    def __init__(self, db):
        self.db = db

    def request(self, user_id: int, chat_id: int, name: str | None, username: str | None) -> str:
        if self.db.execute("INSERT IGNORE INTO tg_members (user_id, chat_id, name, username, status, requested_at) "
                           "VALUES (%s, %s, %s, %s, 'pending', NOW(3))", (user_id, chat_id, (name or "")[:128] or None,
                                                                      (username or "")[:64] or None)) == 1:
            return "new"
        row = self.db.fetch_one("SELECT status FROM tg_members WHERE user_id = %s", (user_id,))
        return row["status"] if row else "new"

    def pending(self) -> list[dict]:
        return self.db.fetch_all("SELECT user_id, chat_id, name, username, requested_at FROM tg_members WHERE status = 'pending' "
                                 "ORDER BY requested_at")

    def get(self, user_id: int) -> dict | None:
        return self.db.fetch_one("SELECT * FROM tg_members WHERE user_id = %s", (user_id,))

    def approve(self, user_id: int, by: int) -> int:
        return self.db.execute("UPDATE tg_members SET status = 'approved', decided_at = NOW(3), decided_by = %s "
                               "WHERE user_id = %s AND status = 'pending'", (by, user_id))

    def reject(self, user_id: int) -> int:
        return self.db.execute("DELETE FROM tg_members WHERE user_id = %s AND status = 'pending'", (user_id,))

    def is_member(self, user_id: int | None) -> bool:
        return user_id is not None and self.db.fetch_one(
            "SELECT 1 AS x FROM tg_members WHERE user_id = %s AND status = 'approved'", (user_id,)) is not None

    def chats(self) -> list[int]:
        return [int(r["chat_id"]) for r in self.db.fetch_all("SELECT chat_id FROM tg_members WHERE status = 'approved'")]
