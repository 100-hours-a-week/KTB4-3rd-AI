# P6 보존 정리(리더, 매일): 기간 지난 행 삭제(LIMIT 5000 반복), 대화 벡터 정리, 정리본 만료, 색인 보정.
import logging
import time

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


def reindex(ctx) -> dict:
    k, chat, out = ctx.repos.knowledge, ctx.repos.chat, {"knowledge": 0, "chat": 0, "version": 0}
    if k.vec is None:
        return out
    try:
        k.vec.delete_older("chat", "ts", int(time.time()) - ctx.settings.section("retention").get("chat_days", 365) * 86400)
    except Exception as exc:
        log.warning("chat vector purge failed: error=%s", type(exc).__name__)
    for item in k.pending():
        out["knowledge"] += k.index(item)
    for row in chat.pending():
        if chat.index(row["chat_id"], row["message_id"], row["question"], row["answer"] or "", row["incident_id"], row["intent"]):
            chat.mark_indexed(row["chat_id"], row["message_id"])
            out["chat"] += 1
    try:
        active = k.active()
        payloads = k.vec.payloads("knowledge", [f"knowledge:{a['id']}" for a in active])
        for a in active:
            if payloads.get(f"knowledge:{a['id']}", {}).get("version") != a["version"]:
                out["version"] += k.index(a)
    except Exception as exc:
        log.warning("version check failed: error=%s", type(exc).__name__)
    return out


def run(ctx) -> dict:
    result = {"purged": purge(ctx), "reindexed": reindex(ctx)}
    log.info("retention done: %s", result)
    return result
