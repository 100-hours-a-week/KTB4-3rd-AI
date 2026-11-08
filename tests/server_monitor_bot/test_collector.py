# P1 수집: 버퍼 → 저장소 옮기기, 실패 시 버퍼 유지.
from types import SimpleNamespace

import pytest

from app.Server_Monitor_Bot.pipelines.collector import Collector
from app.Server_Monitor_Bot.store.buffer import Outbox


class Sink:
    def __init__(self, fail=False):
        self.fail, self.saved = fail, {}

    def __getattr__(self, name):
        def save(rows):
            if self.fail:
                raise RuntimeError("db down")
            self.saved[name] = self.saved.get(name, 0) + len(rows)
            return len(rows)
        return save


def ctx(sink):
    return SimpleNamespace(repos=SimpleNamespace(records=sink, metrics=sink), stats={}, telegram=None)


def test_moves_rows_and_keeps_on_failure(tmp_path):
    box = Outbox(tmp_path / "buffer.db")
    box.put_many([("request_result", {"request_id": "r1", "ts": 1_700_000_000.0, "feature": "receipt", "step_id": "respond",
                                      "result": "ok", "latency_ms": 10}),
                  ("llm_call", {"request_id": "r1", "ts": 1_700_000_000.0, "input_tokens": 5, "output_tokens": 2}),
                  ("worker_stats", {"inflight": 2, "dropped": 0, "orphan": 0})])
    failing = Collector(ctx(Sink(fail=True)), box)
    with pytest.raises(RuntimeError):
        failing.tick()
    assert box.count() == 3 and failing.failing_since
    ok = Sink()
    c = ctx(ok)
    assert Collector(c, box).tick() == 3 and box.count() == 0
    assert ok.saved["save_request_results"] == 1 and ok.saved["upsert"] == 1 and c.stats["worker"]["inflight"] == 2
