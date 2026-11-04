# 조정(adjustments)과 공유 플래그(runtime_flags).
from .base import dt, insert_with_id, js, unjs


class Adjust:
    def __init__(self, db):
        self.db = db

    def propose(self, *, incident_id: str | None, knowledge_id: str | None, knob: str, old_value, new_value, ttl_min: int) -> str:
        return insert_with_id(self.db, "A",
                              "INSERT INTO adjustments (id, created_at, incident_id, knowledge_id, knob, old_value, new_value, ttl_min, status) "
                              "VALUES (%s, NOW(3), %s, %s, %s, %s, %s, %s, 'proposed')",
                              (incident_id, knowledge_id, knob, js(old_value), js(new_value), ttl_min))

    def get(self, adj_id: str) -> dict | None:
        row = self.db.fetch_one("SELECT * FROM adjustments WHERE id = %s", (adj_id,))
        if row:
            for k in ("old_value", "new_value", "before_metrics", "after_metrics"):
                row[k] = unjs(row[k])
        return row

    def active_for_knob(self, knob: str, proposed_ttl_h: int = 24) -> dict | None:
        return self.db.fetch_one("SELECT id, status FROM adjustments WHERE knob = %s AND (status IN ('applied', 'kept') OR "
                                 "(status = 'proposed' AND created_at >= NOW(3) - INTERVAL %s HOUR)) ORDER BY created_at DESC LIMIT 1",
                                 (knob, proposed_ttl_h))

    def set_status(self, adj_id: str, status: str, expect: tuple | None = None, **fields) -> int:
        sets, params = ["status = %s"], [status]
        for k, v in fields.items():
            sets.append(f"{k} = %s")
            params.append(js(v) if k.endswith("_metrics") else v)
        if status in ("rejected", "rolled_back", "expired"):
            sets.append("ended_at = NOW(3)")
        where = f" AND status IN ({', '.join(['%s'] * len(expect))})" if expect else ""
        return self.db.execute(f"UPDATE adjustments SET {', '.join(sets)} WHERE id = %s{where}", (*params, adj_id, *(expect or ())))

    def apply(self, adj_id: str, knob: str, value, expires_at: float, before_metrics: dict) -> bool:
        with self.db.transaction() as cur:
            if cur.execute("UPDATE adjustments SET status = 'applied', applied_at = NOW(3), before_metrics = %s "
                           "WHERE id = %s AND status = 'proposed'", (js(before_metrics), adj_id)) != 1:
                return False
            cur.execute("INSERT INTO runtime_flags (`key`, value, expires_at, set_by, reason, adjustment_id) VALUES (%s, %s, %s, 'S4b', %s, %s) AS new "
                        "ON DUPLICATE KEY UPDATE value = new.value, expires_at = new.expires_at, set_by = new.set_by, reason = new.reason, "
                        "adjustment_id = new.adjustment_id", (knob, js(value), dt(expires_at), f"adjustment {adj_id}", adj_id))
        return True

    def rollback(self, adj_id: str, knob: str, status: str = "rolled_back") -> bool:
        with self.db.transaction() as cur:
            if cur.execute("UPDATE adjustments SET status = %s, ended_at = NOW(3) WHERE id = %s AND status IN ('applied', 'kept')",
                           (status, adj_id)) != 1:
                return False
            cur.execute("DELETE FROM runtime_flags WHERE `key` = %s AND adjustment_id = %s", (knob, adj_id))
        return True

    def set_flag(self, key: str, value, expires_at: float | None, set_by: str, reason: str | None = None) -> None:
        self.db.execute("INSERT INTO runtime_flags (`key`, value, expires_at, set_by, reason) VALUES (%s, %s, %s, %s, %s) AS new "
                        "ON DUPLICATE KEY UPDATE value = new.value, expires_at = new.expires_at, set_by = new.set_by, reason = new.reason",
                        (key, js(value), dt(expires_at) if expires_at else None, set_by, reason))

    def delete_flag(self, key: str) -> None:
        self.db.execute("DELETE FROM runtime_flags WHERE `key` = %s", (key,))

    def flags(self) -> list[dict]:
        rows = self.db.fetch_all("SELECT `key`, value, expires_at, set_by, reason, adjustment_id FROM runtime_flags")
        for r in rows:
            r["value"] = unjs(r["value"])
        return rows

    def expire_due(self) -> list[dict]:
        due = self.db.fetch_all("SELECT `key`, adjustment_id, set_by FROM runtime_flags WHERE expires_at IS NOT NULL AND expires_at <= NOW(3)")
        won = []
        for r in due:
            with self.db.transaction() as cur:
                if cur.execute("DELETE FROM runtime_flags WHERE `key` = %s AND expires_at <= NOW(3)", (r["key"],)) != 1:
                    continue
                if r["adjustment_id"]:
                    cur.execute("UPDATE adjustments SET status = 'expired', ended_at = NOW(3) WHERE id = %s AND status IN ('applied', 'kept')",
                                (r["adjustment_id"],))
            won.append(r)
        return won
