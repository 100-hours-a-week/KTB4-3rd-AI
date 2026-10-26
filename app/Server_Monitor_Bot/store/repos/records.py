# 관측 기록 저장·조회: request_results, llm_calls, app_logs, resource_samples, monitor_health.
from .base import dt, epoch

RESULT_COLS = ("request_id", "ts", "task_id", "feature", "result", "code", "step_id", "reason", "status", "error_code", "route",
               "route_reason", "fallback", "fallback_step", "latency_ms", "image_bytes", "image_type", "image_host", "attempts", "drill")
LLM_COLS = ("ts", "request_id", "feature", "name", "device", "model", "prompt_ver", "ms", "result", "reason", "status",
            "error_code", "input_tokens", "output_tokens", "attempts", "openai_req")
LOG_COLS = ("ts", "task_id", "level", "logger", "head", "message", "request_id", "exc")


def _row(cols, r: dict, defaults: dict | None = None) -> tuple:
    d = defaults or {}
    return tuple(dt(r["ts"]) if c == "ts" else (r.get(c) if r.get(c) is not None else d.get(c)) for c in cols)


class Records:
    def __init__(self, db):
        self.db = db

    def save_request_results(self, rows: list[dict]) -> int:
        cols = ", ".join(RESULT_COLS)
        marks = ", ".join(["%s"] * len(RESULT_COLS))
        update = ", ".join(f"{c} = new.{c}" for c in RESULT_COLS if c != "request_id")
        return self.db.insert_many(f"INSERT INTO request_results ({cols}) VALUES ({marks}) AS new ON DUPLICATE KEY UPDATE {update}",
                                   [_row(RESULT_COLS, r, {"attempts": 1, "fallback": False, "drill": False, "latency_ms": 0}) for r in rows])

    def save_llm_calls(self, rows: list[dict]) -> int:
        return self.db.insert_many(f"INSERT INTO llm_calls ({', '.join(LLM_COLS)}) VALUES ({', '.join(['%s'] * len(LLM_COLS))})",
                                   [_row(LLM_COLS, r, {"feature": "other", "model": "-", "prompt_ver": "-", "attempts": 1, "ms": 0}) for r in rows])

    def save_app_logs(self, rows: list[dict]) -> int:
        return self.db.insert_many(f"INSERT INTO app_logs ({', '.join(LOG_COLS)}) VALUES ({', '.join(['%s'] * len(LOG_COLS))})",
                                   [_row(LOG_COLS, r, {"message": "", "head": "-"}) for r in rows])

    def save_resource_sample(self, task_id: str, mem_bytes: int, rss_bytes: int, inflight: int, ts: float | None = None) -> None:
        self.db.execute("INSERT IGNORE INTO resource_samples (task_id, ts, mem_bytes, rss_bytes, inflight) VALUES (%s, %s, %s, %s, %s)",
                        (task_id, dt(ts), mem_bytes, rss_bytes, inflight))

    def save_health(self, row: dict) -> None:
        self.db.execute("INSERT INTO monitor_health (ts, task_id, dropped, orphan, send_fail, restarts, rss_bytes, image_tag) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (dt(row.get("ts")), row["task_id"], row.get("dropped", 0), row.get("orphan", 0), row.get("send_fail", 0),
                         row.get("restarts", 0), row.get("rss_bytes", 0), row.get("image_tag", "unknown")))

    def recent_logs(self, minutes: int = 10, query: str | None = None, limit: int = 50) -> list[dict]:
        minutes = max(1, min(int(minutes), 10))
        if query:
            rows = self.db.fetch_all("SELECT id, ts, level, logger, head, message, request_id FROM app_logs "
                                     "WHERE ts >= UTC_TIMESTAMP(3) - INTERVAL %s MINUTE AND MATCH(message) AGAINST (%s IN BOOLEAN MODE) "
                                     "ORDER BY id DESC LIMIT %s", (minutes, query, limit))
        else:
            rows = self.db.fetch_all("SELECT id, ts, level, logger, head, message, request_id FROM app_logs "
                                     "WHERE ts >= UTC_TIMESTAMP(3) - INTERVAL %s MINUTE ORDER BY id DESC LIMIT %s", (minutes, limit))
        return rows

    def request_trace(self, request_id: str) -> dict:
        result = self.db.fetch_one("SELECT * FROM request_results WHERE request_id = %s", (request_id,))
        logs = self.db.fetch_all("SELECT id, ts, level, head, message FROM app_logs WHERE request_id = %s ORDER BY id LIMIT 50", (request_id,))
        calls = self.db.fetch_all("SELECT id, ts, name, model, ms, result, reason, status, error_code, input_tokens, output_tokens, attempts "
                                  "FROM llm_calls WHERE request_id = %s ORDER BY id", (request_id,))
        return {"result": result, "logs": logs, "llm_calls": calls}

    def oldest_ts(self, table: str) -> float | None:
        row = self.db.fetch_one(f"SELECT MIN(ts) AS ts FROM {table}")
        return epoch(row["ts"]) if row and row["ts"] else None

    def purge(self, table: str, column: str, days: int, extra: str = "") -> int:
        total = 0
        while True:
            n = self.db.execute(f"DELETE FROM {table} WHERE {column} < UTC_TIMESTAMP(3) - INTERVAL %s DAY {extra} LIMIT 5000", (days,))
            total += n
            if n < 5000:
                return total
