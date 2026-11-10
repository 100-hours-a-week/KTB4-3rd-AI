# 장애 훈련 실행(drill_runs)과 채점(eval_results).
from .base import insert_with_id


class Drills:
    def __init__(self, db):
        self.db = db

    def start(self, scenario_id: str, group: str, started_by: int) -> str:
        return insert_with_id(self.db, "R", "INSERT INTO drill_runs (id, scenario_id, `group`, started_by, status, started_at) "
                              "VALUES (%s, %s, %s, %s, 'running', NOW(3))", (scenario_id, group, started_by))

    def current(self) -> dict | None:
        return self.db.fetch_one("SELECT * FROM drill_runs WHERE status IN ('running', 'awaiting_guess') ORDER BY started_at DESC LIMIT 1")

    def get(self, run_id: str) -> dict | None:
        return self.db.fetch_one("SELECT * FROM drill_runs WHERE id = %s", (run_id,))

    def stop(self, run_id: str) -> None:
        self.db.execute("UPDATE drill_runs SET status = 'awaiting_guess', stopped_at = NOW(3) WHERE id = %s", (run_id,))

    def guess(self, run_id: str, n: int, text: str, ms: int, hit: bool | None, reason: str | None) -> None:
        self.db.execute(f"UPDATE drill_runs SET human_guess_{n} = %s, human_ms_{n} = %s, human_hit_{n} = %s, human_judge_reason_{n} = %s "
                        "WHERE id = %s", (text, ms, hit, reason, run_id))
        self.db.execute("UPDATE drill_runs SET status = 'closed' WHERE id = %s AND human_guess_1 IS NOT NULL AND human_guess_2 IS NOT NULL", (run_id,))

    def close_stale(self) -> int:
        return self.db.execute("UPDATE drill_runs SET status = 'closed' WHERE status != 'closed' AND stopped_at <= NOW(3) - INTERVAL 24 HOUR")

    def add_eval(self, row: dict) -> None:
        cols = ("drill_run_id", "scenario_id", "group", "config", "run_no", "expected_step_id", "predicted_step_id", "step_hit",
                "expected_cause_text", "predicted_cause_text", "cause_hit", "judge_reason", "evidence_first_pass", "false_positive",
                "duration_ms", "tokens")
        self.db.execute(f"INSERT INTO eval_results ({', '.join('`group`' if c == 'group' else c for c in cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                        tuple(row.get(c) if c != "false_positive" else bool(row.get(c)) for c in cols))

    def incidents_since(self, started_at) -> int:
        return int(self.db.fetch_one("SELECT COUNT(*) AS n FROM incidents WHERE drill = TRUE AND created_at >= %s", (started_at,))["n"])
