# P9 스케줄러: 즉시·예약 적재, 연쇄 깊이 제한, 꺼내기와 완료 표시.
import time

from app.Server_Monitor_Bot.runtime.context import new_event
from app.Server_Monitor_Bot.runtime.scheduler import Scheduler


class Coord:
    def __init__(self):
        self.scheduled, self.done = [], []

    def schedule(self, event, not_before):
        self.scheduled.append((event, not_before))

    def claim_events(self, task_id, limit=10):
        return [(7, e) for e, _ in self.scheduled]

    def event_done(self, i):
        self.done.append(i)


def test_enqueue_schedule_pull_done():
    c = Coord()
    s = Scheduler(c, "t")
    assert s.enqueue(new_event("user.message"))
    assert s.queue.get_nowait()["type"] == "user.message"
    assert s.enqueue(new_event("adapt.observe"), time.time() + 600) and s.queue.empty()
    assert not s.enqueue(new_event("x", chain_depth=4))
    assert s.pull() == 1
    e = s.queue.get_nowait()
    assert e["source"] == "scheduler"
    s.done(e)
    assert c.done == [7]
