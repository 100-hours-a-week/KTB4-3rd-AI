# P1 수집(3초): 컨테이너 SQLite 버퍼를 MySQL 관측 테이블로 옮기고 1분 집계를 더한다. 실패하면 버퍼를 그대로 둔다.
import time
from collections import defaultdict

from ..channels.telegram import render
from ..store.repos.metrics import bucket_rows

DOWN_ALERT_S = 300


class Collector:
    def __init__(self, ctx, outbox, batch: int = 500):
        self.ctx, self.outbox, self.batch = ctx, outbox, batch
        self.failing_since: float | None = None
        self.alerted = False

    def once(self) -> int:
        rows = self.outbox.read(self.batch)
        if not rows:
            return 0
        by = defaultdict(list)
        for _, kind, payload in rows:
            by[kind].append(payload)
        repos = self.ctx.repos
        repos.records.save_request_results(by["request_result"])
        repos.records.save_llm_calls(by["llm_call"])
        repos.records.save_app_logs(by["app_log"])
        repos.metrics.upsert(bucket_rows(by["request_result"], by["llm_call"]))
        if by["worker_stats"]:
            self.ctx.stats["worker"] = by["worker_stats"][-1]
        self.outbox.delete_upto(rows[-1][0])
        return len(rows)

    def tick(self) -> int:
        moved = 0
        try:
            while True:
                n = self.once()
                moved += n
                if n < self.batch:
                    break
            self.failing_since, self.alerted = None, False
        except Exception:
            now = time.time()
            self.failing_since = self.failing_since or now
            if not self.alerted and now - self.failing_since >= DOWN_ALERT_S and self.ctx.telegram:
                self.ctx.telegram.send(None, render.store_down(int((now - self.failing_since) // 60)))
                self.alerted = True
            raise
        return moved

    def drain(self, deadline_s: float = 10.0) -> None:
        end = time.monotonic() + deadline_s
        while time.monotonic() < end:
            try:
                if self.once() == 0:
                    return
            except Exception:
                return
