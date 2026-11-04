# P6 보존 정리(리더, 매일): 기간 지난 행 삭제(LIMIT 5000 반복), 대화 벡터 정리·보정, 정리본 만료, 지식·문서 동기화 재확인.
import logging
import time

from . import knowledge_seed

log = logging.getLogger("app.Server_Monitor_Bot.retention")


def purge(ctx) -> dict:
    r, days = ctx.repos.records, ctx.settings.section("retention")
    out = {}
    for table in ("request_results", "llm_calls", "app_logs", "resource_samples"):
        out[table] = r.purge(table, "ts", days.get("raw_days", 30))
    out["metrics_1m"] = r.purge("metrics_1m", "bucket", days.get("metrics_days", 365))
    out["alerts"] = r.purge("alerts", "last_at", days.get("alerts_days", 7))
    for table in ("agent_runs", "monitor_health"):
        out[table] = r.purge(table, "ts", days.get("runs_days", 90))
    out["tg_messages"] = r.purge("tg_messages", "ts", days.get("tg_days", 30))
    out["chat_messages"] = r.purge("chat_messages", "ts", days.get("chat_days", 365))
    ctx.repos.knowledge.expire_drafts()
    out["knowledge_drafts"] = r.purge("knowledge_drafts", "COALESCE(decided_at, expires_at)", days.get("drafts_days", 30), "AND status != 'pending'")
    out["scheduled_events"] = r.purge("scheduled_events", "done_at", days.get("events_days", 7), "AND done_at IS NOT NULL")
    out["drills_closed"] = ctx.repos.drills.close_stale()
    return out


def rebuild(vec, collection: str, repo) -> bool:
    try:
        if vec.count(collection) > 0:
            return False
    except Exception as exc:
        log.warning("vector count failed: collection=%s error=%s", collection, type(exc).__name__)
        return False
    return repo.mark_all_pending() > 0


def reindex(ctx) -> dict:
    chat, out = ctx.repos.chat, {"chat": 0}
    if chat.vec is None:
        return out
    try:
        chat.vec.delete_older("chat", "ts", int(time.time()) - ctx.settings.section("retention").get("chat_days", 365) * 86400)
    except Exception as exc:
        log.warning("chat vector purge failed: error=%s", type(exc).__name__)
    out["rebuilt"] = ["chat"] if rebuild(chat.vec, "chat", chat) else []
    while rows := chat.pending():
        done = 0
        for row in rows:
            if chat.index(row["chat_id"], row["message_id"], row["question"], row["answer"] or "", row["incident_id"], row["intent"],
                          row["ts"]):
                chat.mark_indexed(row["chat_id"], row["message_id"])
                done += 1
        out["chat"] += done
        if done < len(rows):
            break
    try:
        out["sync"] = knowledge_seed.sync(ctx)
    except Exception as exc:
        log.warning("knowledge sync failed: error=%s", type(exc).__name__)
    return out


def run(ctx) -> dict:
    result = {"purged": purge(ctx), "reindexed": reindex(ctx)}
    log.info("retention done: %s", result)
    return result
