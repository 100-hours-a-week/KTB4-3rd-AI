# 장애 지식: 조회·검색·색인, 정리본 저장, [저장] 트랜잭션, 시드 적재.
import logging
import time

from ...knowledge.schema import FIELDS, body, validate
from .base import dt, insert_with_id, js, unjs

log = logging.getLogger("app.Server_Monitor_Bot.repos")
JSON_COLS = ("features", "step_ids", "match", "checks")


def _load(row: dict | None) -> dict | None:
    if row:
        for c in JSON_COLS:
            row[c] = unjs(row[c])
    return row


def embed_text(k: dict) -> str:
    return f"{k['title']}\n{k['symptom']}\n{k['match']}\n{k['cause']}"


class Knowledge:
    def __init__(self, db, vec=None, embed=None, policy_keys=()):
        self.db, self.vec, self.embed, self.policy_keys = db, vec, embed, tuple(policy_keys)

    def active(self) -> list[dict]:
        return [_load(r) for r in self.db.fetch_all("SELECT * FROM knowledge WHERE status = 'active'")]

    def get(self, kid: str) -> dict | None:
        return _load(self.db.fetch_one("SELECT * FROM knowledge WHERE id = %s", (kid,)))

    def titles(self, query: str | None = None, limit: int = 30) -> list[dict]:
        if query:
            like = f"%{query}%"
            return self.db.fetch_all("SELECT id, title, version, source FROM knowledge WHERE status = 'active' AND "
                                     "(id LIKE %s OR title LIKE %s OR cause LIKE %s) ORDER BY id LIMIT %s", (like, like, like, limit))
        return self.db.fetch_all("SELECT id, title, version, source FROM knowledge WHERE status = 'active' ORDER BY id LIMIT %s", (limit,))

    def search(self, query: str, *, hide: list[str] = (), step_ids: list[str] = (), limit: int = 5, min_score: float = 0.0) -> list[dict]:
        try:
            hits = self.vec.search("knowledge", self.embed.one(query), limit=limit, equals={"status": "active"},
                                   none_of={"knowledge_id": list(hide)})
            ids = [h["knowledge_id"] for h in hits if h["score"] >= min_score]
            scores = {h["knowledge_id"]: h["score"] for h in hits}
        except Exception as exc:
            log.warning("knowledge search fallback: error=%s", type(exc).__name__)
            rows = [k for k in self.active() if k["id"] not in hide and (not step_ids or set(step_ids) & set(k["step_ids"]))]
            for k in rows[:limit]:
                k["score"], k["fallback"] = None, True
            return rows[:limit]
        if not ids:
            return []
        rows = {r["id"]: _load(r) for r in self.db.fetch_all("SELECT * FROM knowledge WHERE id IN %s AND status = 'active'", (tuple(ids),))}
        out = []
        for kid in ids:
            if kid in rows:
                rows[kid]["score"] = scores[kid]
                out.append(rows[kid])
        return out

    def index(self, k: dict) -> bool:
        try:
            if k["status"] != "active":
                self.vec.delete("knowledge", f"knowledge:{k['id']}")
            else:
                self.vec.upsert("knowledge", f"knowledge:{k['id']}", self.embed.one(embed_text(k)),
                                {"knowledge_id": k["id"], "status": k["status"], "source": k["source"], "features": k["features"],
                                 "step_ids": k["step_ids"], "alert": k["alert"], "severity": k["severity"],
                                 "policy_key": k["policy_key"], "version": k["version"]})
            self.db.execute("UPDATE knowledge SET embed_pending = FALSE WHERE id = %s", (k["id"],))
            return True
        except Exception as exc:
            log.warning("knowledge index failed: id=%s error=%s", k["id"], type(exc).__name__)
            self.db.execute("UPDATE knowledge SET embed_pending = TRUE WHERE id = %s", (k["id"],))
            return False

    def pending(self) -> list[dict]:
        return [_load(r) for r in self.db.fetch_all("SELECT * FROM knowledge WHERE embed_pending = TRUE")]

    def save_draft(self, *, op: str, target_id: str | None, target_version: int | None, draft_body: dict, reason: str,
                   source_refs: list, incident_id: str | None, chat_id: int, ttl_h: int = 24) -> str:
        return insert_with_id(self.db, "D",
                              "INSERT INTO knowledge_drafts (id, op, target_id, target_version, body, reason, source_refs, incident_id, chat_id, "
                              "created_at, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(3), NOW(3) + INTERVAL %s HOUR)",
                              (op, target_id, target_version, js(draft_body), reason, js(source_refs), incident_id, chat_id, ttl_h))

    def get_draft(self, draft_id: str) -> dict | None:
        row = self.db.fetch_one("SELECT * FROM knowledge_drafts WHERE id = %s", (draft_id,))
        if row:
            row["body"], row["source_refs"] = unjs(row["body"]), unjs(row["source_refs"])
        return row

    def cancel_draft(self, draft_id: str, user_id: int) -> bool:
        return self.db.execute("UPDATE knowledge_drafts SET status = 'cancelled', decided_by = %s, decided_at = NOW(3) "
                               "WHERE id = %s AND status = 'pending'", (user_id, draft_id)) == 1

    def expire_drafts(self) -> int:
        return self.db.execute("UPDATE knowledge_drafts SET status = 'expired' WHERE status = 'pending' AND expires_at < NOW(3)")

    def apply_draft(self, draft_id: str, user_id: int) -> dict:
        with self.db.transaction() as cur:
            cur.execute("SELECT * FROM knowledge_drafts WHERE id = %s FOR UPDATE", (draft_id,))
            d = cur.fetchone()
            if not d or d["status"] != "pending":
                return {"ok": False, "message": "이미 처리했거나 없는 정리본입니다"}
            if d["expires_at"] < dt():
                cur.execute("UPDATE knowledge_drafts SET status = 'expired' WHERE id = %s", (draft_id,))
                return {"ok": False, "message": "정리본이 만료됐습니다(24시간). 다시 정리해 달라고 말해 주세요"}
            item, op = unjs(d["body"]), d["op"]
            if op != "delete":
                errors = validate({**item, "id": item.get("id") or "KN_CHECK"}, self.policy_keys)
                if errors:
                    return {"ok": False, "message": "정리본 검증 실패: " + "; ".join(errors[:3])}
            if op == "add":
                cur.execute("SELECT MAX(CAST(SUBSTRING(id, 5) AS UNSIGNED)) AS n FROM knowledge WHERE id LIKE 'KN\\_C%' FOR UPDATE")
                kid = f"KN_C{(cur.fetchone()['n'] or 0) + 1:06d}"
                cur.execute("INSERT INTO knowledge (id, title, features, step_ids, `match`, symptom, cause, resolution, checks, policy_key, alert, "
                            "severity, source, status, version, updated_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'chat', 'active', 1, %s)",
                            (kid, item["title"], js(item.get("features") or []), js(item.get("step_ids") or []), js(item["match"]),
                             item["symptom"], item["cause"], item["resolution"], js(item.get("checks") or []), item.get("policy_key"),
                             item["alert"], item["severity"], str(user_id)))
            else:
                kid = d["target_id"]
                cur.execute("SELECT * FROM knowledge WHERE id = %s AND status = 'active' FOR UPDATE", (kid,))
                cur_row = cur.fetchone()
                if not cur_row:
                    return {"ok": False, "message": f"{kid}가 없거나 이미 삭제됐습니다"}
                if d["target_version"] is not None and cur_row["version"] != d["target_version"]:
                    return {"ok": False, "message": "그사이 지식이 바뀌었습니다. 다시 정리해 주세요"}
                old = _load(dict(cur_row))
                cur.execute("INSERT INTO knowledge_versions (knowledge_id, version, body, changed_by, change_reason, draft_id) VALUES (%s, %s, %s, %s, %s, %s)",
                            (kid, old["version"], js(body(old)), str(user_id), d["reason"], draft_id))
                if op == "update":
                    cur.execute("UPDATE knowledge SET title=%s, features=%s, step_ids=%s, `match`=%s, symptom=%s, cause=%s, resolution=%s, checks=%s, "
                                "policy_key=%s, alert=%s, severity=%s, source='chat', version = version + 1, updated_by=%s WHERE id = %s",
                                (item["title"], js(item.get("features") or []), js(item.get("step_ids") or []), js(item["match"]), item["symptom"],
                                 item["cause"], item["resolution"], js(item.get("checks") or []), item.get("policy_key"), item["alert"],
                                 item["severity"], str(user_id), kid))
                else:
                    cur.execute("UPDATE knowledge SET status='deleted', source='chat', version = version + 1, updated_by=%s WHERE id = %s",
                                (str(user_id), kid))
            if d["incident_id"]:
                cur.execute("UPDATE incidents SET status='resolved', knowledge_id=%s, resolved_at=NOW(3) WHERE id = %s AND status = 'open'",
                            (kid, d["incident_id"]))
            cur.execute("UPDATE knowledge_drafts SET status='saved', decided_by=%s, decided_at=NOW(3) WHERE id = %s", (user_id, draft_id))
        k = self.get(kid)
        indexed = self.index(k)
        return {"ok": True, "knowledge": k, "indexed": indexed, "op": op,
                "message": f"{'삭제' if op == 'delete' else '저장'}했습니다: {k['title']} ({kid} v{k['version']})"
                           + ("" if indexed else " · 검색 반영 지연")}

    def seed_done(self, items: list[dict], seed_hash: str) -> bool:
        rows = {r["id"]: r for r in self.db.fetch_all("SELECT id, source, seed_hash FROM knowledge WHERE id IN %s",
                                                       (tuple(i["id"] for i in items),))} if items else {}
        return all(i["id"] in rows and (rows[i["id"]]["seed_hash"] == seed_hash or rows[i["id"]]["source"] == "chat") for i in items)

    def upsert_seed(self, items: list[dict], seed_hash: str) -> dict:
        stats = {"inserted": 0, "updated": 0, "unchanged": 0, "diverged": [], "index_failed": 0}
        existing = {r["id"]: _load(r) for r in self.db.fetch_all("SELECT * FROM knowledge")}
        for item in items:
            cur = existing.get(item["id"])
            if cur and cur["source"] == "chat":
                if body(cur) != body(item):
                    stats["diverged"].append(item["id"])
                continue
            params = (item["title"], js(item.get("features") or []), js(item.get("step_ids") or []), js(item["match"]), item["symptom"],
                      item["cause"], item["resolution"], js(item.get("checks") or []), item.get("policy_key"), item["alert"], item["severity"])
            if not cur:
                self.db.execute("INSERT INTO knowledge (id, title, features, step_ids, `match`, symptom, cause, resolution, checks, policy_key, alert, "
                                "severity, source, status, version, seed_hash, updated_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                                "'seed', 'active', 1, %s, 'seed')", (item["id"], *params, seed_hash))
                stats["inserted"] += 1
            elif body(cur) != body(item) or cur["status"] != "active":
                self.db.execute("INSERT IGNORE INTO knowledge_versions (knowledge_id, version, body, changed_by, change_reason) VALUES (%s, %s, %s, 'seed', 'seed update')",
                                (cur["id"], cur["version"], js(body(cur))))
                self.db.execute("UPDATE knowledge SET title=%s, features=%s, step_ids=%s, `match`=%s, symptom=%s, cause=%s, resolution=%s, checks=%s, "
                                "policy_key=%s, alert=%s, severity=%s, status='active', version = version + 1, seed_hash=%s, updated_by='seed' WHERE id = %s",
                                (*params, seed_hash, item["id"]))
                stats["updated"] += 1
            else:
                self.db.execute("UPDATE knowledge SET seed_hash = %s WHERE id = %s", (seed_hash, item["id"]))
                stats["unchanged"] += 1
                continue
            if not self.index(self.get(item["id"])):
                stats["index_failed"] += 1
        stats["removed"] = [kid for kid, k in existing.items() if k["source"] == "seed" and kid not in {i["id"] for i in items}]
        return stats
