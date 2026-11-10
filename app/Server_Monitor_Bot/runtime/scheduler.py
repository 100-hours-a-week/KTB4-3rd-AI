# P9 이벤트 큐·스케줄러: 시스템 이벤트(알림·후속)를 사람 메시지보다 먼저 꺼내고, 사람 메시지는 최대 20건·같은 질문 60초 중복 거절·접수 안내를 한다. 예약 이벤트는 scheduled_events에 넣고 리더가 5초마다 꺼낸다.
import itertools
import logging
import queue
import threading
import time

from ..channels.telegram import render

log = logging.getLogger("app.Server_Monitor_Bot.scheduler")
MAX_DEPTH = 3
ACK_TYPES = ("user.message", "user.trace", "action.doc_upload")


class Scheduler:
    def __init__(self, coord=None, task_id: str = "local", sender=None, user_max: int = 20, dedupe_s: int = 60):
        self.coord, self.task_id, self.sender = coord, task_id, sender
        self.user_max, self.dedupe_s = user_max, dedupe_s
        self.queue: "queue.PriorityQueue[tuple]" = queue.PriorityQueue()
        self.seq, self.lock = itertools.count(), threading.Lock()
        self.waiting, self.recent = 0, {}

    def reply(self, event: dict, text: str):
        if self.sender is None or event.get("chat_id") is None:
            return None
        try:
            return self.sender.send(event["chat_id"], text, reply_to=(event.get("payload") or {}).get("message_id"))
        except Exception as exc:
            log.warning("queue reply failed: error=%s", type(exc).__name__)
            return None

    def admit(self, event: dict) -> bool:
        p, now = event.get("payload") or {}, time.time()
        key = (event.get("chat_id"), event.get("from_id"), p.get("text") or p.get("file_name"))
        with self.lock:
            self.recent = {k: t for k, t in self.recent.items() if now - t < self.dedupe_s}
            if event["type"] in ACK_TYPES and key in self.recent:
                self.reply(event, render.say("queue_dup"))
                return False
            if self.waiting >= self.user_max:
                self.reply(event, render.say("queue_full", n=self.user_max))
                return False
            self.waiting += 1
            if event["type"] in ACK_TYPES:
                self.recent[key] = now
                mid = self.reply(event, render.say("queue_ack", n=self.waiting))
                if mid:
                    event["ack"] = (event["chat_id"], mid)
            self.queue.put((1, next(self.seq), event))
        return True

    def enqueue(self, event: dict, not_before: float | None = None) -> bool:
        if event.get("chain_depth", 0) > MAX_DEPTH:
            log.info("event dropped: type=%s reason=chain_depth", event.get("type"))
            return False
        if event.get("source") == "telegram":
            return self.admit(event)
        if not_before is None or not_before <= time.time() + 1:
            self.queue.put((0, next(self.seq), event))
            return True
        try:
            self.coord.schedule(event, not_before)
            return True
        except Exception as exc:
            log.warning("schedule failed: type=%s error=%s", event.get("type"), type(exc).__name__)
            return False

    def get(self, timeout: float | None = None) -> dict:
        prio, _, event = self.queue.get(timeout=timeout) if timeout else self.queue.get_nowait()
        if prio:
            with self.lock:
                self.waiting -= 1
        return event

    def pull(self) -> int:
        n = 0
        for row_id, event in self.coord.claim_events(self.task_id):
            self.queue.put((0, next(self.seq), {**event, "source": "scheduler", "_scheduled_id": row_id}))
            n += 1
        return n

    def done(self, event: dict) -> None:
        if event.get("_scheduled_id") is not None:
            self.coord.event_done(event["_scheduled_id"])
