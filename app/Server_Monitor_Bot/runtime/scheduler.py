# P9 이벤트 큐·스케줄러: 후속 이벤트를 메모리 큐 또는 scheduled_events에 넣고, 리더가 5초마다 실행 시각이 된 것을 꺼낸다.
import logging
import queue
import time

log = logging.getLogger("app.Server_Monitor_Bot.scheduler")
MAX_DEPTH = 3


class Scheduler:
    def __init__(self, coord=None, task_id: str = "local"):
        self.coord, self.task_id = coord, task_id
        self.queue: "queue.Queue[dict]" = queue.Queue()

    def enqueue(self, event: dict, not_before: float | None = None) -> bool:
        if event.get("chain_depth", 0) > MAX_DEPTH:
            log.info("event dropped: type=%s reason=chain_depth", event.get("type"))
            return False
        if not_before is None or not_before <= time.time() + 1:
            self.queue.put(event)
            return True
        try:
            self.coord.schedule(event, not_before)
            return True
        except Exception as exc:
            log.warning("schedule failed: type=%s error=%s", event.get("type"), type(exc).__name__)
            return False

    def pull(self) -> int:
        n = 0
        for row_id, event in self.coord.claim_events(self.task_id):
            self.queue.put({**event, "source": "scheduler", "_scheduled_id": row_id})
            n += 1
        return n

    def done(self, event: dict) -> None:
        if event.get("_scheduled_id") is not None:
            self.coord.event_done(event["_scheduled_id"])
