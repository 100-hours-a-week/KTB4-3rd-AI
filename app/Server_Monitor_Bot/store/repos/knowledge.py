# 장애 지식: 원본은 Qdrant <stage>_knowledge(현재 active·deleted, 이전 버전 archived). 정리본은 MySQL, [저장]은 이름 잠금 안에서 처리한다.
import logging
import re
from contextlib import contextmanager
from datetime import datetime, timezone

from ...knowledge.schema import body, load_seed, signal_text, validate
from .base import dt, insert_with_id, js, unjs

log = logging.getLogger("app.Server_Monitor_Bot.repos")
C = "knowledge"
GONE = {"ok": False, "message": "이미 처리했거나 없는 정리본입니다"}


def embed_text(k: dict) -> str:
    return f"{k['title']}\n{k['symptom']}\n{signal_text(k.get('match') or {})}\n{k['cause']}"


def key(kid: str, version: int | None = None) -> str:
    return f"knowledge:{kid}" + (f"@v{version}" if version else "")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Knowledge:
    def __init__(self, db, vec=None, embed=None, policy_keys=(), docs=None):
        self.db, self.vec, self.embed, self.policy_keys, self.docs = db, vec, embed, tuple(policy_keys), docs

    def payload(self, item: dict, kid: str, version: int, source: str, by, seed_hash: str | None = None, draft_id: str | None = None) -> dict:
        return {**body(item), "id": kid, "knowledge_id": kid, "version": version, "source": source, "status": "active",
                "seed_hash": seed_hash, "updated_by": str(by), "updated_at": now(), "draft_id": draft_id}

    def seed(self) -> list[dict]:
        try:
            items, _ = load_seed()
        except Exception:
            return []
        return [{**i, "knowledge_id": i["id"], "source": "seed", "status": "active", "version": 1, "fallback": True}
                for i in items if not validate(i, self.policy_keys)]

    def current(self) -> list[dict]:
        return self.vec.scroll(C, none_of={"status": ["archived"]})

    def active(self) -> list[dict]:
        try:
            return self.vec.scroll(C, equals={"status": "active"})
        except Exception as exc:
            log.warning("knowledge read fallback: error=%s", type(exc).__name__)
            return self.seed()

    def get(self, kid: str) -> dict | None:
        return self.vec.payloads(C, [key(kid)]).get(key(kid))

    def titles(self, query: str | None = None, limit: int = 30) -> list[dict]:
        q = (query or "").lower()
        rows = [k for k in self.active() if not q or any(q in str(k.get(f) or "").lower() for f in ("id", "title", "cause"))]
        return [{f: k.get(f) for f in ("id", "title", "version", "source")} for k in sorted(rows, key=lambda k: k["id"])[:limit]]

    def search(self, query: str, *, hide: list[str] = (), step_ids: list[str] = (), limit: int = 5, min_score: float = 0.0,
               vector: list[float] | None = None) -> list[dict]:
        try:
            vector = vector or self.embed.one(query)
            hits = []
            if step_ids:
                hits = [h for h in self.vec.search(C, vector, limit=limit, equals={"status": "active"}, any_of={"step_ids": list(step_ids)},
                                                   none_of={"knowledge_id": list(hide)}) if h["score"] >= min_score]
            if len(hits) < limit:
                seen = [h["id"] for h in hits]
                hits += [h for h in self.vec.search(C, vector, limit=limit - len(hits), equals={"status": "active"},
                                                    none_of={"knowledge_id": [*hide, *seen]}) if h["score"] >= min_score]
            return hits
        except Exception as exc:
            log.warning("knowledge search fallback: error=%s", type(exc).__name__)
            rows = [k for k in self.seed() if k["id"] not in hide and (not step_ids or set(step_ids) & set(k["step_ids"]))]
            return [{**k, "score": None} for k in rows[:limit]]

    @contextmanager
    def lock(self):
        name = f"server_bot:{self.vec.stage}:knowledge"[:64]
        conn = self.db.lock_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT GET_LOCK(%s, 10) AS got", (name,))
                if not cur.fetchone()["got"]:
                    raise TimeoutError("knowledge lock")
            yield
        finally:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT RELEASE_LOCK(%s)", (name,))
            finally:
                conn.close()

    def archive(self, cur: dict, reason: str, by, draft_id: str | None) -> None:
        old = self.vec.vectors(C, [key(cur["id"])]).get(key(cur["id"]))
        self.vec.upsert(C, key(cur["id"], cur["version"]), old, {**cur, "status": "archived", "change_reason": reason,
                                                                   "archived_by": str(by), "archived_at": now(), "draft_id": draft_id})

    def save_draft(self, *, op: str, target_id: str | None, target_version: int | None, draft_body: dict, reason: str,
                   source_refs: list, incident_id: str | None, chat_id: int, ttl_h: int = 24, kind: str = "knowledge") -> str:
        return insert_with_id(self.db, "D",
                              "INSERT INTO knowledge_drafts (id, kind, op, target_id, target_version, body, reason, source_refs, incident_id, "
                              "chat_id, created_at, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(3), NOW(3) + INTERVAL %s HOUR)",
                              (kind, op, target_id, target_version, js(draft_body), reason, js(source_refs), incident_id, chat_id, ttl_h))

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
        try:
            with self.lock():
                d = self.get_draft(draft_id)
                if not d or d["status"] != "pending":
                    return GONE
                if d["expires_at"] < dt():
                    self.db.execute("UPDATE knowledge_drafts SET status = 'expired' WHERE id = %s AND status = 'pending'", (draft_id,))
                    return {"ok": False, "message": "정리본이 만료됐습니다(24시간). 다시 정리해 달라고 말해 주세요"}
                res = self.apply_doc(d, user_id) if d.get("kind") == "doc" else self.apply_knowledge(d, user_id)
                if not res.get("ok"):
                    return res
                with self.db.transaction() as cur:
                    if d["incident_id"] and d.get("kind") != "doc":
                        cur.execute("UPDATE incidents SET status='resolved', knowledge_id=%s, resolved_at=NOW(3) WHERE id = %s AND status = 'open'",
                                    (res["knowledge"]["id"], d["incident_id"]))
                    cur.execute("UPDATE knowledge_drafts SET status='saved', decided_by=%s, decided_at=NOW(3) WHERE id = %s AND status = 'pending'",
                                (user_id, draft_id))
                return res
        except Exception as exc:
            log.warning("draft apply failed: id=%s error=%s", draft_id, type(exc).__name__)
            return {"ok": False, "message": "저장소 오류로 저장하지 못했습니다. 정리본은 그대로 있으니 다시 눌러 주세요"}

    def apply_knowledge(self, d: dict, user_id: int) -> dict:
        item, op, draft_id = d["body"], d["op"], d["id"]
        if op != "delete":
            errors = validate({**item, "id": item.get("id") or "KN_CHECK"}, self.policy_keys)
            if errors:
                return {"ok": False, "message": "정리본 검증 실패: " + "; ".join(errors[:3])}
        if op == "add":
            done = self.vec.scroll(C, equals={"draft_id": draft_id, "status": "active"})
            kid = done[0]["id"] if done else None
            if not kid:
                vector = self.embed_or_none(item)
                if vector is None:
                    return {"ok": False, "message": "임베딩에 실패해 저장하지 못했습니다. 정리본은 그대로 있으니 다시 눌러 주세요"}
                n = max((int(k["id"][4:]) for k in self.current() if re.fullmatch(r"KN_C\d{6}", k["id"])), default=0)
                kid = f"KN_C{n + 1:06d}"
                self.vec.upsert(C, key(kid), vector, self.payload(item, kid, 1, "chat", user_id, draft_id=draft_id))
        else:
            kid = d["target_id"]
            cur = self.get(kid)
            if not (cur and cur.get("draft_id") == draft_id):
                if not cur or cur["status"] != "active":
                    return {"ok": False, "message": f"{kid}가 없거나 이미 삭제됐습니다"}
                if d["target_version"] is not None and cur["version"] != d["target_version"]:
                    return {"ok": False, "message": "그사이 지식이 바뀌었습니다. 다시 정리해 주세요"}
                if op == "update":
                    vector = self.embed_or_none(item)
                    if vector is None:
                        return {"ok": False, "message": "임베딩에 실패해 저장하지 못했습니다. 정리본은 그대로 있으니 다시 눌러 주세요"}
                    self.archive(cur, d["reason"], user_id, draft_id)
                    self.vec.upsert(C, key(kid), vector, self.payload(item, kid, cur["version"] + 1, "chat", user_id, draft_id=draft_id))
                else:
                    self.archive(cur, d["reason"], user_id, draft_id)
                    self.vec.set_payload(C, key(kid), {"status": "deleted", "source": "chat", "version": cur["version"] + 1,
                                                       "updated_by": str(user_id), "updated_at": now(), "draft_id": draft_id})
        k = self.get(kid)
        return {"ok": True, "knowledge": k, "indexed": True, "op": op,
                "message": f"{'삭제' if op == 'delete' else '저장'}했습니다: {k['title']} ({kid} v{k['version']})"}

    def apply_doc(self, d: dict, user_id: int) -> dict:
        if self.docs is None:
            return {"ok": False, "message": "문서 저장소에 연결되지 않았습니다"}
        old = self.docs.get(d["target_id"])
        if not old:
            return {"ok": False, "message": "그사이 문서가 바뀌었거나 없는 절입니다. 다시 요청해 주세요"}
        if d["op"] == "delete":
            self.docs.delete_keys([d["target_id"]])
        elif self.docs.replace(d["target_id"], d["body"]["text"], user_id) is None:
            return {"ok": False, "message": "그사이 문서가 바뀌었거나 없는 절입니다. 다시 요청해 주세요"}
        word = "삭제" if d["op"] == "delete" else "수정"
        return {"ok": True, "doc": old, "op": d["op"], "message": f"{word}했습니다: {old['doc_id']} · {old['section']}"}

    def embed_or_none(self, item: dict) -> list[float] | None:
        try:
            return self.embed.one(embed_text(item))
        except Exception as exc:
            log.warning("knowledge embed failed: error=%s", type(exc).__name__)
            return None

    def seed_done(self, items: list[dict], seed_hash: str) -> bool:
        rows = {k["id"]: k for k in self.current()}
        return all(i["id"] in rows and (rows[i["id"]].get("seed_hash") == seed_hash or rows[i["id"]]["source"] == "chat") for i in items)

    def upsert_seed(self, items: list[dict], seed_hash: str) -> dict:
        stats = {"inserted": 0, "updated": 0, "unchanged": 0, "diverged": [], "index_failed": 0}
        existing = {k["id"]: k for k in self.current()}
        todo = []
        for item in items:
            cur = existing.get(item["id"])
            if cur and cur["source"] == "chat":
                if body(cur) != body(item):
                    stats["diverged"].append(item["id"])
                continue
            if cur and cur["status"] == "active" and body(cur) == body(item):
                if cur.get("seed_hash") != seed_hash:
                    self.vec.set_payload(C, key(item["id"]), {"seed_hash": seed_hash})
                stats["unchanged"] += 1
                continue
            todo.append((item, cur))
        try:
            vectors = self.embed.many([embed_text(i) for i, _ in todo]) if todo else []
        except Exception as exc:
            log.warning("seed embed failed: error=%s", type(exc).__name__)
            vectors, stats["index_failed"] = [], len(todo)
        for (item, cur), vector in zip(todo, vectors):
            if cur:
                self.archive(cur, "seed update", "seed", None)
                stats["updated"] += 1
            else:
                stats["inserted"] += 1
            self.vec.upsert(C, key(item["id"]), vector, self.payload(item, item["id"], cur["version"] + 1 if cur else 1, "seed", "seed", seed_hash))
        ids = {i["id"] for i in items}
        stats["removed"] = [kid for kid, k in existing.items() if k["source"] == "seed" and k["status"] == "active" and kid not in ids]
        return stats
