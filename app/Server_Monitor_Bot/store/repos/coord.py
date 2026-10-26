# 태스크 간 조율: 알림 선점, 예약 이벤트, 텔레그램 수신 위치·메시지 연결, 실행 기록.
from .base import dt, js, unjs


class Coord:
    def __init__(self, db):
        self.db = db

    def claim_alert(self, key: str, rank: int, count: int, task_id: str, quiet_s: int = 600) -> bool:
        if self.db.execute("INSERT IGNORE INTO alerts (alert_key, last_at, `rank`, `count`, task_id) VALUES (%s, NOW(3), %s, %s, %s)",
                           (key, rank, count, task_id)) == 1:
            return True
        if self.db.execute("UPDATE alerts SET last_at = NOW(3), `rank` = %s, `count` = %s, held = 0, task_id = %s WHERE alert_key = %s AND "
                           "(last_at <= NOW(3) - INTERVAL %s SECOND OR `rank` < %s OR %s >= `count` * 3)",
                           (rank, count, task_id, key, quiet_s, rank, count)) == 1:
            return True
        self.db.execute("UPDATE alerts SET held = held + %s WHERE alert_key = %s", (count, key))
        return False

    def held(self, key: str) -> int:
        row = self.db.fetch_one("SELECT held FROM alerts WHERE alert_key = %s", (key,))
        return int(row["held"]) if row else 0

    def schedule(self, event: dict, not_before: float) -> int:
        return self.db.insert("INSERT INTO scheduled_events (event, not_before) VALUES (%s, %s)", (js(event), dt(not_before)))

    def claim_events(self, task_id: str, limit: int = 10) -> list[tuple[int, dict]]:
        now = dt()
        stamp = now.replace(microsecond=now.microsecond // 1000 * 1000)
        if not self.db.execute("UPDATE scheduled_events SET claimed_by = %s, claimed_at = %s WHERE done_at IS NULL AND not_before <= NOW(3) "
                               "AND (claimed_by IS NULL OR claimed_at <= NOW(3) - INTERVAL 10 MINUTE) ORDER BY not_before LIMIT %s",
                               (task_id, stamp, limit)):
            return []
        rows = self.db.fetch_all("SELECT id, event FROM scheduled_events WHERE claimed_by = %s AND claimed_at = %s AND done_at IS NULL",
                                 (task_id, stamp))
        return [(r["id"], unjs(r["event"])) for r in rows]

    def event_done(self, event_id: int) -> None:
        self.db.execute("UPDATE scheduled_events SET done_at = NOW(3) WHERE id = %s", (event_id,))

    def tg_offset(self, stage: str) -> int:
        row = self.db.fetch_one("SELECT last_update_id FROM tg_state WHERE stage = %s", (stage,))
        return int(row["last_update_id"]) if row else 0

    def set_tg_offset(self, stage: str, update_id: int) -> None:
        self.db.execute("INSERT INTO tg_state (stage, last_update_id) VALUES (%s, %s) AS new "
                        "ON DUPLICATE KEY UPDATE last_update_id = new.last_update_id", (stage, update_id))

    def link_message(self, message_id: int, chat_id: int, ref_kind: str, ref_id: str) -> None:
        self.db.execute("INSERT IGNORE INTO tg_messages (message_id, chat_id, ref_kind, ref_id, ts) VALUES (%s, %s, %s, %s, NOW(3))",
                        (message_id, chat_id, ref_kind, str(ref_id)[:255]))

    def ref_of(self, chat_id: int, message_id: int) -> tuple[str, str] | None:
        row = self.db.fetch_one("SELECT ref_kind, ref_id FROM tg_messages WHERE chat_id = %s AND message_id = %s", (chat_id, message_id))
        return (row["ref_kind"], row["ref_id"]) if row else None

    def seen_event(self, event_id: str, within_s: int = 3600) -> bool:
        return self.db.fetch_one("SELECT 1 AS x FROM agent_runs WHERE event_id = %s AND ts >= NOW(3) - INTERVAL %s SECOND",
                                 (event_id, within_s)) is not None

    def record_run(self, row: dict) -> None:
        self.db.execute(
            "INSERT IGNORE INTO agent_runs (run_id, event_id, ts, event_type, route, drill, node_path, input_tokens, output_tokens, tokens, "
            "ms, tool_ms, detect_to_send_ms, outcome, error, image_tag) VALUES (%s, %s, NOW(3), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (row["run_id"], row["event_id"], row["event_type"], row.get("route"), bool(row.get("drill")), js(row.get("node_path", [])),
             row.get("input_tokens", 0), row.get("output_tokens", 0), row.get("tokens", 0), row.get("ms", 0), row.get("tool_ms", 0),
             row.get("detect_to_send_ms"), row.get("outcome", "ok"), (row.get("error") or None) and str(row["error"])[:255],
             row.get("image_tag", "unknown")))

    def tokens_today(self, drill: bool) -> int:
        row = self.db.fetch_one("SELECT COALESCE(SUM(tokens), 0) AS t FROM agent_runs WHERE drill = %s AND ts >= UTC_DATE()", (drill,))
        return int(row["t"])

    def schema_version(self) -> int:
        row = self.db.fetch_one("SELECT MAX(version) AS v FROM schema_version")
        return int(row["v"] or 0)
