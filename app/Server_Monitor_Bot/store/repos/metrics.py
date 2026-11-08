# 집계: metrics_1m 누적, 감지 윈도우, 작업 도구용 단계·LLM·의존 대상·배포·리소스 요약, 일일 리포트.
from collections import defaultdict

from .base import dt

DEPENDENCIES = {"image_host": ("image_fetch",), "openai": ("model_ready", "luna_call"), "modal": ("pipe_ocr",)}


def p95(values: list[int]) -> int | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]


def bucket_rows(results: list[dict], calls: list[dict]) -> list[tuple]:
    tokens = defaultdict(int)
    for c in calls:
        if c.get("request_id"):
            tokens[c["request_id"]] += (c.get("input_tokens") or 0) + (c.get("output_tokens") or 0)
    groups: dict[tuple, dict] = {}
    for r in results:
        if r.get("drill"):
            continue
        minute = dt(r["ts"] - r["ts"] % 60)
        g = groups.setdefault((minute, r["feature"], r["step_id"]), {"calls": 0, "fails": 0, "fallbacks": 0, "tokens": 0, "ms": []})
        g["calls"] += 1
        g["fails"] += r.get("result") == "fail"
        g["fallbacks"] += bool(r.get("fallback"))
        g["tokens"] += tokens.get(r["request_id"], 0)
        g["ms"].append(r.get("latency_ms") or 0)
    return [(b, f, s, g["calls"], g["fails"], g["fallbacks"], g["tokens"], p95(g["ms"])) for (b, f, s), g in groups.items()]


class Metrics:
    def __init__(self, db):
        self.db = db

    def upsert(self, rows: list[tuple]) -> int:
        return self.db.insert_many(
            "INSERT INTO metrics_1m (bucket, feature, step_id, calls, fails, fallbacks, tokens, p95_ms) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) AS new "
            "ON DUPLICATE KEY UPDATE calls = metrics_1m.calls + new.calls, fails = metrics_1m.fails + new.fails, "
            "fallbacks = metrics_1m.fallbacks + new.fallbacks, tokens = metrics_1m.tokens + new.tokens, "
            "p95_ms = GREATEST(COALESCE(metrics_1m.p95_ms, 0), COALESCE(new.p95_ms, 0))", rows)

    def window(self, seconds: int = 300, drill: bool = False) -> dict:
        groups = self.db.fetch_all(
            "SELECT feature, step_id, reason, status, error_code, COUNT(*) AS n, SUM(result = 'fail') AS fails, "
            "SUM(fallback) AS fallbacks, MAX(latency_ms) AS max_ms FROM request_results "
            "WHERE ts >= UTC_TIMESTAMP(3) - INTERVAL %s SECOND AND drill = %s GROUP BY feature, step_id, reason, status, error_code",
            (seconds, drill))
        totals = {r["feature"]: int(r["n"]) for r in self.db.fetch_all(
            "SELECT feature, COUNT(*) AS n FROM request_results WHERE ts >= UTC_TIMESTAMP(3) - INTERVAL %s SECOND AND drill = %s GROUP BY feature",
            (seconds, drill))}
        llm = {r["feature"]: r for r in self.db.fetch_all(
            "SELECT feature, COUNT(*) AS n, AVG(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)) AS tokens_per_call, MAX(ms) AS max_ms "
            "FROM llm_calls WHERE ts >= UTC_TIMESTAMP(3) - INTERVAL %s SECOND GROUP BY feature", (seconds,))}
        return {"groups": groups, "totals": totals, "llm": llm}

    def step_failures(self, feature: str, step_id: str, minutes: int = 15, drill: bool = False) -> dict:
        minutes = max(1, min(int(minutes), 60))
        def span(start: int, end: int) -> dict:
            total = self.db.fetch_one("SELECT COUNT(*) AS n FROM request_results WHERE feature = %s AND drill = %s AND "
                                      "ts >= UTC_TIMESTAMP(3) - INTERVAL %s MINUTE AND ts < UTC_TIMESTAMP(3) - INTERVAL %s MINUTE",
                                      (feature, drill, start, end))["n"]
            reasons = self.db.fetch_all("SELECT reason, status, COUNT(*) AS n FROM request_results WHERE feature = %s AND step_id = %s "
                                        "AND result = 'fail' AND drill = %s AND ts >= UTC_TIMESTAMP(3) - INTERVAL %s MINUTE "
                                        "AND ts < UTC_TIMESTAMP(3) - INTERVAL %s MINUTE GROUP BY reason, status ORDER BY n DESC LIMIT 5",
                                        (feature, step_id, drill, start, end))
            fails = sum(int(r["n"]) for r in reasons)
            return {"total": int(total), "fails": fails, "rate": round(fails / total, 4) if total else 0.0, "reasons": reasons}
        return {"feature": feature, "step_id": step_id, "minutes": minutes, "now": span(minutes, 0), "before": span(2 * minutes, minutes)}

    def overview(self, minutes: int = 60) -> dict:
        minutes = max(1, min(int(minutes), 1440))
        since = "ts >= UTC_TIMESTAMP(3) - INTERVAL %s MINUTE"
        features = self.db.fetch_all(f"SELECT feature, COUNT(*) AS total, SUM(result = 'fail') AS fails FROM request_results "
                                     f"WHERE drill = FALSE AND {since} GROUP BY feature ORDER BY feature", (minutes,))
        top = self.db.fetch_all(f"SELECT feature, step_id, reason, status, COUNT(*) AS n FROM request_results WHERE result = 'fail' "
                                f"AND drill = FALSE AND {since} GROUP BY feature, step_id, reason, status ORDER BY n DESC LIMIT 3", (minutes,))
        logs = self.db.fetch_one(f"SELECT SUM(level = 'WARNING') AS warnings, SUM(level IN ('ERROR', 'CRITICAL')) AS errors "
                                 f"FROM app_logs WHERE {since}", (minutes,)) or {}
        return {"minutes": minutes, "features": [{"feature": r["feature"], "total": int(r["total"]), "fails": int(r["fails"] or 0)} for r in features],
                "top_failures": top, "warnings": int(logs.get("warnings") or 0), "errors": int(logs.get("errors") or 0)}

    def llm_summary(self, feature: str, minutes: int = 15) -> dict:
        minutes = max(1, min(int(minutes), 60))
        rows = self.db.fetch_all("SELECT id, ms, result, reason, status, error_code, attempts, model, COALESCE(input_tokens, 0) + "
                                 "COALESCE(output_tokens, 0) AS tokens FROM llm_calls WHERE feature = %s AND "
                                 "ts >= UTC_TIMESTAMP(3) - INTERVAL %s MINUTE ORDER BY id DESC LIMIT 2000", (feature, minutes))
        if not rows:
            return {"feature": feature, "minutes": minutes, "calls": 0, "sample_ids": []}
        ms = [r["ms"] for r in rows]
        codes: dict[str, int] = defaultdict(int)
        for r in rows:
            if r["result"] != "ok":
                codes[f"{r['status']}/{r['error_code'] or r['reason']}"] += 1
        failed = [r["id"] for r in rows if r["result"] != "ok"][:3] or [r["id"] for r in rows[:3]]
        return {"feature": feature, "minutes": minutes, "calls": len(rows), "errors": dict(codes),
                "p50_ms": sorted(ms)[len(ms) // 2], "p95_ms": p95(ms),
                "retry_rate": round(sum(1 for r in rows if (r["attempts"] or 1) > 1) / len(rows), 4),
                "tokens_per_call": round(sum(r["tokens"] for r in rows) / len(rows), 1),
                "models": sorted({r["model"] for r in rows}), "sample_ids": failed}

    def dependency_failures(self, dep: str, minutes: int = 15) -> dict:
        steps = DEPENDENCIES.get(dep)
        if not steps:
            return {"error": f"unknown dependency {dep}"}
        minutes = max(1, min(int(minutes), 60))
        rows = self.db.fetch_all("SELECT feature, step_id, SUM(calls) AS calls, SUM(fails) AS fails FROM metrics_1m WHERE bucket >= "
                                 "UTC_TIMESTAMP() - INTERVAL %s MINUTE GROUP BY feature, step_id", (minutes,))
        totals = defaultdict(int)
        for r in rows:
            totals[r["feature"]] += int(r["calls"])
        hit = [{"feature": r["feature"], "step_id": r["step_id"], "fails": int(r["fails"]), "total": totals[r["feature"]]}
               for r in rows if r["step_id"] in steps and int(r["fails"]) > 0]
        return {"dependency": dep, "steps": list(steps), "minutes": minutes, "failing": hit}

    def resource_trend(self, minutes: int = 30) -> list[dict]:
        minutes = max(1, min(int(minutes), 60))
        return self.db.fetch_all("SELECT task_id, MAX(mem_bytes) AS mem_max, MAX(rss_bytes) AS rss_max, MAX(inflight) AS inflight_max, "
                                 "MAX(ts) AS last_ts FROM resource_samples WHERE ts >= UTC_TIMESTAMP(3) - INTERVAL %s MINUTE GROUP BY task_id",
                                 (minutes,))

    def deploy_changes(self, hours: int = 24) -> list[dict]:
        hours = max(1, min(int(hours), 24))
        tags = self.db.fetch_all("SELECT image_tag, MIN(ts) AS first_ts FROM monitor_health WHERE ts >= UTC_TIMESTAMP(3) - INTERVAL %s HOUR "
                                 "GROUP BY image_tag ORDER BY first_ts", (hours,))
        out = []
        for t in tags:
            rows = self.db.fetch_all("SELECT step_id, SUM(ts < %s) AS before_n, SUM(ts >= %s) AS after_n FROM request_results "
                                     "WHERE result = 'fail' AND drill = FALSE AND ts BETWEEN %s - INTERVAL 1 HOUR AND %s + INTERVAL 1 HOUR "
                                     "GROUP BY step_id", (t["first_ts"],) * 4)
            out.append({"image_tag": t["image_tag"], "first_ts": t["first_ts"], "fails_by_step": rows})
        return out

    def compare(self, feature: str, step_id: str, start, end) -> dict:
        same = "feature = %s AND drill = FALSE AND ts >= %s AND ts < %s AND (result != 'fail' OR step_id = %s)"
        row = self.db.fetch_one(f"SELECT COUNT(*) AS calls, SUM(result = 'fail') AS fails, MAX(latency_ms) AS max_ms "
                                f"FROM request_results WHERE {same}", (feature, start, end, step_id))
        ms = [r["latency_ms"] for r in self.db.fetch_all(f"SELECT latency_ms FROM request_results WHERE {same}", (feature, start, end, step_id))]
        calls, fails = int(row["calls"] or 0), int(row["fails"] or 0)
        return {"calls": calls, "fails": fails, "fail_rate": fails / calls if calls else 0.0, "p95_ms": p95(ms)}

    def daily(self, day_start, day_end) -> dict:
        calls = self.db.fetch_all("SELECT feature, COUNT(*) AS calls, SUM(result != 'ok') AS fails, SUM(attempts > 1) AS retried, "
                                  "SUM(COALESCE(input_tokens, 0)) AS input_tokens, SUM(COALESCE(output_tokens, 0)) AS output_tokens, "
                                  "AVG(ms) AS avg_ms, MAX(ms) AS max_ms FROM llm_calls WHERE ts >= %s AND ts < %s GROUP BY feature",
                                  (day_start, day_end))
        reasons = self.db.fetch_all("SELECT feature, step_id, reason, COUNT(*) AS n FROM request_results WHERE result = 'fail' AND drill = FALSE "
                                    "AND ts >= %s AND ts < %s GROUP BY feature, step_id, reason ORDER BY n DESC LIMIT 10", (day_start, day_end))
        monitor = self.db.fetch_one("SELECT COALESCE(SUM(tokens), 0) AS tokens, COUNT(*) AS runs, SUM(outcome != 'ok') AS failed "
                                    "FROM agent_runs WHERE ts >= %s AND ts < %s", (day_start, day_end))
        health = self.db.fetch_one("SELECT COALESCE(MAX(send_fail), 0) AS send_fail, COALESCE(MAX(restarts), 0) AS restarts, "
                                   "COALESCE(MAX(dropped), 0) AS dropped FROM monitor_health WHERE ts >= %s AND ts < %s", (day_start, day_end))
        return {"llm": calls, "fail_reasons": reasons, "monitor": monitor, "health": health}

    def samples(self, feature: str, step_id: str, reason: str | None, drill: bool = False, seconds: int = 300, limit: int = 5) -> list[str]:
        rows = self.db.fetch_all("SELECT request_id FROM request_results WHERE feature = %s AND step_id = %s AND result = 'fail' AND drill = %s "
                                 "AND (reason = %s OR (%s IS NULL AND reason IS NULL)) AND ts >= UTC_TIMESTAMP(3) - INTERVAL %s SECOND "
                                 "ORDER BY ts DESC LIMIT %s", (feature, step_id, drill, reason, reason, seconds, limit))
        return [r["request_id"] for r in rows]

    def signals(self, hours: int = 24) -> list[dict]:
        return self.db.fetch_all("SELECT feature, step_id, reason, status, error_code, COUNT(*) AS n FROM request_results "
                                 "WHERE result = 'fail' AND drill = FALSE AND ts >= UTC_TIMESTAMP(3) - INTERVAL %s HOUR "
                                 "GROUP BY feature, step_id, reason, status, error_code ORDER BY n DESC", (hours,))
