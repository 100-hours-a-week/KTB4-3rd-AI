# 장애 기록(incidents) 저장·조회·해결 처리.
from .base import insert_with_id, js, unjs


class Incidents:
    def __init__(self, db):
        self.db = db

    def create(self, *, trigger_key: str, feature: str | None, step_id: str | None, summary: str, cause_text: str,
               resolution_text: str | None, confidence: str, evidence: list, knowledge_refs: list, policy_key: str | None,
               drill: bool, agent_run_id: str | None) -> str:
        return insert_with_id(self.db, "I",
                              "INSERT INTO incidents (id, created_at, trigger_key, feature, step_id, summary, cause_text, resolution_text, "
                              "confidence, evidence, knowledge_refs, policy_key, drill, agent_run_id) VALUES "
                              "(%s, NOW(3), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                              (trigger_key, feature, step_id, summary, cause_text, resolution_text, confidence, js(evidence),
                               js(knowledge_refs), policy_key, drill, agent_run_id))

    def get(self, incident_id: str) -> dict | None:
        row = self.db.fetch_one("SELECT * FROM incidents WHERE id = %s", (incident_id,))
        if row:
            row["evidence"], row["knowledge_refs"] = unjs(row["evidence"]), unjs(row["knowledge_refs"])
        return row

    def recent_for_key(self, trigger_key: str, seconds: int = 3600) -> dict | None:
        return self.db.fetch_one("SELECT id, confidence, created_at FROM incidents WHERE trigger_key = %s AND "
                                 "created_at >= NOW(3) - INTERVAL %s SECOND ORDER BY created_at DESC LIMIT 1", (trigger_key, seconds))

    def open_count(self) -> int:
        return int(self.db.fetch_one("SELECT COUNT(*) AS n FROM incidents WHERE status = 'open' AND drill = FALSE")["n"])
