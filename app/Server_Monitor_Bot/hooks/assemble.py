# 조립 쓰레드: 로그 줄을 request_id별로 묶어 'request done'에서 request_result를 확정하고 SQLite 버퍼에 쓴다.
import queue
import re
import socket
import threading
import time

from . import state
from .mask import mask

DETAIL = re.compile(r"(\w+)=(\S+)")
FEATURES = {"/receipt": "receipt", "/taxi_meter": "taxi_meter"}
CODE_STEP = {
    "OK": "respond", "REQUEST_INVALID": "request_validate", "IMAGE_FETCH_FAILED": "image_fetch",
    "IMAGE_SIZE_INVALID": "image_validate", "IMAGE_TYPE_INVALID": "image_validate", "MODEL_NOT_READY": "model_ready",
    "UPSTREAM_FAILED": "luna_call", "JUDGE_PARSE_FAILED": "luna_parse", "INTERNAL": "unhandled", "QUEUE_TIMEOUT": "queue_wait",
}
FALLBACK_STEP = {"model_not_ready": "model_ready", "upstream_failed": "pipe_ocr", "internal": "pipe_ocr", "judge_fallback": "pipe_judge"}
REASON_HEADS = {
    "image_fetch": ("image download failed",),
    "image_validate": ("image size is invalid", "image must be jpeg or png"),
    "model_ready": ("openai api key is missing", "model marked down"),
    "luna_call": ("openai request failed", "openai response shape is unexpected", "modal call failed"),
    "luna_parse": ("model output is not valid",),
    "unhandled": ("unhandled error",),
    "queue_wait": ("queue job expired",),
}
BAG_TTL = 180


def split(message: str) -> tuple[str, dict[str, str]]:
    head, _, rest = message.partition(":")
    return head.strip(), dict(DETAIL.findall(rest))


def task_id() -> str:
    return socket.gethostname()


def reason_of(step: str, events: list[tuple[str, dict]]) -> dict:
    heads = REASON_HEADS.get(step, ())
    for head, d in reversed(events):
        if head in heads:
            if head == "image size is invalid":
                return {"reason": "size", "status": None, "error_code": None}
            if head == "openai api key is missing":
                return {"reason": "key_missing", "status": None, "error_code": None}
            if head == "model marked down":
                return {"reason": "model_down", "status": None, "error_code": None}
            if head == "openai response shape is unexpected":
                return {"reason": "shape", "status": None, "error_code": None}
            if head == "queue job expired":
                return {"reason": "expired", "status": None, "error_code": None}
            if head == "unhandled error":
                return {"reason": d.get("error", "error"), "status": None, "error_code": None}
            status = d.get("status")
            code = (d.get("error") or "-/-").split("/")[-1]
            return {"reason": d.get("reason"), "status": int(status) if status and status.isdigit() else None,
                    "error_code": None if code in ("-", "") else code}
    return {"reason": None, "status": None, "error_code": None}


def build_result(rid: str, bag: dict, done: dict, ts: float, attempts: int) -> dict | None:
    feature = FEATURES.get(done.get("path", ""))
    if not feature:
        return None
    code = done.get("code", "INTERNAL")
    step = CODE_STEP.get(code, "unhandled")
    route_reason = bag.get("route_reason")
    image = bag.get("image", {})
    return {
        "request_id": rid, "ts": ts, "task_id": task_id(), "feature": feature,
        "result": "ok" if code == "OK" else "fail", "code": code, "step_id": step,
        **reason_of(step, bag["events"]),
        "route": bag.get("route"), "route_reason": route_reason,
        "fallback": route_reason in FALLBACK_STEP, "fallback_step": FALLBACK_STEP.get(route_reason),
        "latency_ms": int(done.get("ms", 0) or 0), "image_bytes": int(image["bytes"]) if image.get("bytes", "").isdigit() else None,
        "image_type": image.get("type"), "image_host": image.get("host"), "attempts": attempts, "drill": rid.startswith("drill-"),
    }


class Assembler(threading.Thread):
    def __init__(self, outbox, stats_every: float = 60.0):
        super().__init__(name="server-bot-assembler", daemon=True)
        self.outbox = outbox
        self.stats_every = stats_every
        self.bags: dict[str, dict] = {}
        self.seen: dict[str, tuple[float, int]] = {}
        self.stop_event = threading.Event()

    def run(self) -> None:
        pending: list[tuple[str, dict]] = []
        last_flush = last_stats = time.monotonic()
        while not self.stop_event.is_set():
            try:
                kind, item = state.QUEUE.get(timeout=0.5)
                pending.extend(self.handle(kind, item))
            except queue.Empty:
                pass
            now = time.monotonic()
            if pending and (len(pending) >= 200 or now - last_flush >= 1.0):
                self.flush(pending)
                pending, last_flush = [], now
            if now - last_stats >= self.stats_every:
                pending.append(("worker_stats", self.stats()))
                last_stats = now
        self.flush(pending)

    def flush(self, rows: list[tuple[str, dict]]) -> None:
        try:
            self.outbox.put_many(rows)
        except Exception:
            state.STATS["dropped"] += len(rows)

    def stats(self) -> dict:
        cutoff = time.time() - BAG_TTL
        for rid in [r for r, b in self.bags.items() if b["started"] < cutoff]:
            del self.bags[rid]
            state.STATS["orphan"] += 1
        return {"ts": time.time(), "task_id": task_id(), "dropped": state.STATS["dropped"],
                "orphan": state.STATS["orphan"], "inflight": len(self.bags)}

    def handle(self, kind: str, item: dict) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        if kind == "llm_call":
            rid = item.get("request_id")
            if rid and rid in self.bags:
                self.bags[rid].setdefault("llm", []).append(item)
                return out
            return [("llm_call", {**item, "feature": "other"})]
        head, details = split(item["message"])
        rid = item.get("request_id")
        if item["level"] in ("WARNING", "ERROR", "CRITICAL"):
            out.append(("app_log", {"ts": item["ts"], "task_id": task_id(), "level": item["level"], "logger": item["logger"],
                                    "head": head[:191], "message": mask(item["message"]), "request_id": rid, "exc": mask(item["exc"])}))
        if not rid:
            return out
        bag = self.bags.setdefault(rid, {"started": item["ts"], "events": []})
        if head == "request route":
            bag["route"], bag["route_reason"] = details.get("route"), details.get("reason")
        elif head == "image loaded":
            bag["image"] = details
        elif head == "request done":
            self.bags.pop(rid, None)
            last, n = self.seen.get(rid, (0.0, 0))
            n = n + 1 if item["ts"] - last < 600 else 1
            self.seen[rid] = (item["ts"], n)
            if len(self.seen) > 5000:
                self.seen = {k: v for k, v in self.seen.items() if item["ts"] - v[0] < 600}
            result = build_result(rid, bag, details, item["ts"], n)
            if result:
                out.append(("request_result", result))
            feature = result["feature"] if result else "other"
            out.extend(("llm_call", {**call, "feature": feature}) for call in bag.get("llm", []))
            return out
        bag["events"].append((head, details))
        if head in ("image download failed", "image must be jpeg or png") and "host" in details:
            bag.setdefault("image", {})["host"] = details["host"]
        return out
