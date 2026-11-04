# P3 감지(60초, 5분 윈도우): 알림 키별 등급 → 지식 신호 맞춤 → 즉시 알림·진단 이벤트·정책 신호·요약 알림을 낸다.
import json
import logging
import time
from pathlib import Path

from ..channels.telegram import render
from ..knowledge.schema import SEVERITIES, best
from ..runtime.context import new_event

log = logging.getLogger("app.Server_Monitor_Bot.detector")
HIGH, MEDIUM, LOW = 3, 2, 1
DRILLS = Path(__file__).resolve().parents[1] / "drills"


def key_of(feature: str, step_id: str, reason, status, error_code, drill: bool) -> str:
    part = error_code or (f"{reason}_{status}" if status and reason else reason or status or "-")
    return f"{'drill.' if drill else ''}{feature}.{step_id}.{part}"


def items(window: dict, drill: bool) -> list[dict]:
    out = []
    totals = window["totals"]
    fallbacks: dict[str, int] = {}
    for g in window["groups"]:
        total = totals.get(g["feature"], 0)
        fallbacks[g["feature"]] = fallbacks.get(g["feature"], 0) + int(g.get("fallbacks") or 0)
        fails = int(g.get("fails") or 0)
        if not fails:
            continue
        signal = {"feature": g["feature"], "step_id": g["step_id"], "reason": g.get("reason"), "status": g.get("status"),
                  "error_code": g.get("error_code"), "ms": g.get("max_ms"), "ratio": fails / total if total else 0.0,
                  "fails": fails, "total": total}
        out.append({"key": key_of(g["feature"], g["step_id"], g.get("reason"), g.get("status"), g.get("error_code"), drill),
                    "signal": signal, "drill": drill})
    for feature, n in fallbacks.items():
        total = totals.get(feature, 0)
        if n:
            out.append({"key": f"{'drill.' if drill else ''}{feature}.fallback.-", "drill": drill, "fallback": True,
                        "signal": {"feature": feature, "step_id": "fallback", "reason": None, "status": None, "error_code": None,
                                   "ms": None, "ratio": n / total if total else 0.0, "fails": n, "total": total}})
    return out


def grade(item: dict, min_sample: int = 20) -> int:
    s = item["signal"]
    step, n, total, ratio = s["step_id"], s["fails"], s["total"], s["ratio"]
    if item.get("fallback"):
        if total < min_sample:
            return LOW
        return HIGH if ratio >= 0.5 else MEDIUM if ratio >= 0.2 else LOW
    if step in ("unhandled", "model_ready"):
        rank = HIGH
    elif step in ("luna_call", "luna_parse", "queue_wait", "congestion_judge"):
        rank = HIGH if n >= 3 else MEDIUM
    elif step == "request_validate":
        rank = HIGH if n >= 10 else LOW
    elif step in ("image_fetch", "image_validate"):
        rank = HIGH if n >= 5 else LOW
    else:
        rank = MEDIUM
    if total >= min_sample:
        rank = max(rank, HIGH if ratio >= 0.2 else MEDIUM if ratio >= 0.05 else LOW)
    return rank


def decide(rank: int, k: dict | None, recent_same_rank: bool) -> str:
    if k and k.get("alert") == "immediate":
        if SEVERITIES.get(k.get("severity"), 0) == 1 and rank < MEDIUM:
            return "none"
        return "immediate"
    if rank == HIGH:
        return "summary" if recent_same_rank else "diagnose"
    return "summary" if rank == MEDIUM else "none"


class Detector:
    def __init__(self, ctx):
        self.ctx = ctx
        self.cache: list[dict] = []
        self.cached_at = 0.0
        self.last_rank: dict[str, int] = {}

    def knowledge(self) -> list[dict]:
        if time.monotonic() - self.cached_at > 60:
            try:
                self.cache = self.ctx.repos.knowledge.active()
                self.cached_at = time.monotonic()
            except Exception as exc:
                log.warning("knowledge cache failed: error=%s", type(exc).__name__)
        return self.cache

    def samples(self, sig: dict, drill: bool, limit: int = 5) -> list[str]:
        try:
            return self.ctx.repos.metrics.samples(sig["feature"], sig["step_id"], sig["reason"], drill, limit=limit)
        except Exception as exc:
            log.warning("sample lookup failed: error=%s", type(exc).__name__)
            return []

    def drill_info(self) -> dict:
        run = self.ctx.repos.drills.current() or {}
        hide = []
        try:
            hide = json.loads((DRILLS / f"{run.get('scenario_id')}.json").read_text()).get("hide_knowledge", [])
        except (OSError, ValueError):
            pass
        return {"run_id": run.get("id"), "hide_knowledge": hide}

    def tick(self) -> list[tuple[str, str]]:
        cfg = self.ctx.settings.section("detect")
        done = []
        for drill in (False, True):
            if drill and not self.ctx.settings.drill_allowed:
                continue
            window = self.ctx.repos.metrics.window(cfg.get("window_s", 300), drill)
            for item in items(window, drill):
                action = self.handle(item, cfg)
                if action != "none":
                    done.append((item["key"], action))
        return done

    def handle(self, item: dict, cfg: dict) -> str:
        repos, s = self.ctx.repos, item["signal"]
        item["rank"] = rank = grade(item, cfg.get("min_sample", 20))
        k = None if item.get("fallback") else best(self.knowledge(), s)
        recent = repos.incidents.recent_for_key(item["key"], cfg.get("recent_incident_s", 3600))
        action = decide(rank, k, bool(recent) and self.last_rank.get(item["key"]) == rank)
        if item["drill"]:
            action = "diagnose" if action in ("immediate", "diagnose") else "none"
        if action == "none":
            return action
        held = repos.coord.held(item["key"])
        if not repos.coord.claim_alert(item["key"], rank, s["fails"], self.ctx.task_id, cfg.get("quiet_s", 600)):
            return "none"
        item["held"], item["at"] = held, time.time()
        self.last_rank[item["key"]] = rank
        if action == "diagnose":
            self.ctx.enqueue(new_event("alert.pipeline", {
                "alert_key": item["key"], "feature": s["feature"], "step_id": s["step_id"], "reason": s["reason"], "signal": s,
                "summary": f"{render.title(s)} {render.count_line(s, item['at'])}", "rank": rank, "drill": self.drill_info() if item["drill"] else None,
                "sample_ids": self.samples(s, item["drill"]),
                "knowledge_id": k["id"] if k else None, "detected_at": item["at"]}, source="detector"), None)
            return action
        item["samples"] = self.samples(s, item["drill"], 2)
        text = render.alert_known(item, k) if action == "immediate" else render.alert_summary(item)
        telegram = self.ctx.telegram
        sent = telegram.broadcast(text) if telegram else []
        for chat, message_id in sent:
            repos.coord.link_message(message_id, chat, "alert", f"{item['key']}@{int(item['at'])}")
        mid = sent[0][1] if sent else None
        if action == "immediate" and k.get("policy_key") and not item["drill"]:
            self.ctx.enqueue(new_event("signal.policy", {
                "policy_key": k["policy_key"], "knowledge_id": k["id"], "alert_key": item["key"], "message_id": mid,
                "messages": [list(pair) for pair in sent], "message_text": telegram.text(text) if telegram else text, "signal": s}, source="detector"), None)
        return action
