# 배분, 상한, 순차 실행, 예외, 기한, 취소, 종료, 자리 반환, 로그를 가짜 실행 함수로 본다.
import asyncio
import logging
import re

from app.core.exceptions import ModelNotReady, UpstreamError
from app.inner_queue import Scheduler

EMPTY = {"total": 0, "api": 0, "gpu": 0}


class Fake:
    # gate가 열릴 때까지 실행 함수가 멈춘다. fail에 넣은 url은 그 예외를 낸다.
    def __init__(self, fail=None):
        self.gate = asyncio.Event()
        self.fail = fail or {}
        self.calls = []
        self.running = {"api": 0, "gpu": 0}
        self.peak = {"api": 0, "gpu": 0}

    def runner(self, route):
        async def run(url):
            self.calls.append(url)
            self.running[route] += 1
            self.peak[route] = max(self.peak[route], self.running[route])
            try:
                await asyncio.sleep(0.001)
                await self.gate.wait()
                if url in self.fail:
                    raise self.fail[url]
                return {"route": route, "url": url}
            finally:
                self.running[route] -= 1
        return run

    def runs(self):
        return {"taxi_meter": (self.runner("api"), self.runner("gpu"))}


def submit(s, url):
    return asyncio.create_task(s.submit("taxi_meter", url))


async def settle():
    await asyncio.sleep(0.02)


def messages(caplog, prefix):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(prefix)]


def test_routes_api_then_gpu():  # A1
    async def main():
        fake = Fake()
        s = Scheduler(fake.runs())
        await s.start()
        tasks = [submit(s, f"u{i}") for i in range(25)]
        await settle()
        assert s.stats() == {"total": 25, "api": 20, "gpu": 5}
        fake.gate.set()
        routes = [r["route"] for r in await asyncio.gather(*tasks)]
        assert routes == ["api"] * 20 + ["gpu"] * 5
        assert s.stats() == EMPTY
        await s.stop(1)
    asyncio.run(main())


def test_31st_waits_for_slot(caplog):  # A2
    async def main():
        fake = Fake()
        s = Scheduler(fake.runs())
        await s.start()
        tasks = [submit(s, f"u{i}") for i in range(31)]
        await settle()
        assert s.stats() == {"total": 30, "api": 20, "gpu": 10}
        assert len(messages(caplog, "queue job routed")) == 30 and not tasks[30].done()
        fake.gate.set()
        results = await asyncio.gather(*tasks)
        assert results[30]["url"] == "u30" and "u30" in fake.calls
        await s.stop(1)
    with caplog.at_level(logging.INFO, logger="app"):
        asyncio.run(main())


def test_one_at_a_time_per_worker():  # A3
    async def main():
        fake = Fake()
        s = Scheduler(fake.runs())
        await s.start()
        tasks = [submit(s, f"u{i}") for i in range(30)]
        await settle()
        assert fake.running == {"api": 1, "gpu": 1}
        fake.gate.set()
        await asyncio.gather(*tasks)
        assert fake.peak == {"api": 1, "gpu": 1}
        await s.stop(1)
    asyncio.run(main())


def test_errors(caplog):  # A4, A8
    async def main():
        upstream = UpstreamError()
        fake = Fake(fail={"up": upstream, "boom": ValueError("x")})
        fake.gate.set()
        s = Scheduler(fake.runs())
        await s.start()
        try:
            await s.submit("taxi_meter", "up")
        except UpstreamError as exc:
            assert exc is upstream
        else:
            raise AssertionError("expected UpstreamError")
        try:
            await s.submit("taxi_meter", "boom")
        except Exception as exc:
            assert exc.code == "INTERNAL" and exc.status_code == 500
        else:
            raise AssertionError("expected InternalError")
        assert (await s.submit("taxi_meter", "ok"))["url"] == "ok"  # 워커는 살아 있다
        assert s.stats() == EMPTY
        await s.stop(1)
    with caplog.at_level(logging.INFO, logger="app"):
        asyncio.run(main())
    assert messages(caplog, "unhandled error") == ["unhandled error: kind=taxi_meter route=api error=ValueError"]


def test_expired_not_run(caplog):  # A5, A8
    async def main():
        fake = Fake()
        s = Scheduler(fake.runs(), total=3, api=2, gpu=1, deadline=0.2, min_left=0.15)
        await s.start()
        first, late = submit(s, "first"), submit(s, "late")
        await asyncio.sleep(0.08)  # late가 큐에서 0.08초 기다림 → 남은 0.12 < 0.15
        fake.gate.set()
        assert (await first)["url"] == "first"
        try:
            await late
        except Exception as exc:
            assert exc.code == "QUEUE_TIMEOUT" and exc.status_code == 504
        else:
            raise AssertionError("expected QueueTimeout")
        assert fake.calls == ["first"] and s.stats() == EMPTY
        await s.stop(1)
    with caplog.at_level(logging.INFO, logger="app"):
        asyncio.run(main())
    [expired] = messages(caplog, "queue job expired")
    assert re.fullmatch(r"queue job expired: kind=taxi_meter route=api waited_ms=\d+", expired)


def test_cancelled_skipped(caplog):  # A6
    async def main():
        fake = Fake()
        s = Scheduler(fake.runs(), total=3, api=2, gpu=1)
        await s.start()
        first, gone = submit(s, "first"), submit(s, "gone")
        await settle()
        gone.cancel()
        fake.gate.set()
        await first
        await settle()
        assert gone.cancelled() and fake.calls == ["first"] and s.stats() == EMPTY
        await s.stop(1)
    with caplog.at_level(logging.INFO, logger="app"):
        asyncio.run(main())
    assert messages(caplog, "queue job skipped") == ["queue job skipped: kind=taxi_meter route=api reason=cancelled"]


def test_stop_waits_then_rejects():  # A7
    async def main():
        fake = Fake()
        s = Scheduler(fake.runs())
        await s.start()
        job = submit(s, "u")
        await settle()
        stop = asyncio.create_task(s.stop(1))
        await settle()
        try:
            await s.submit("taxi_meter", "new")
        except ModelNotReady:
            pass
        else:
            raise AssertionError("expected ModelNotReady")
        assert not stop.done()  # 처리 중 작업을 기다린다
        fake.gate.set()
        await stop
        assert job.done() and job.result()["url"] == "u"
        assert all(w.done() for w in s.workers) and fake.calls == ["u"]
    asyncio.run(main())


def test_stop_timeout_fails_leftovers():  # A7, A8
    async def main():
        fake = Fake()  # gate를 열지 않는다
        s = Scheduler(fake.runs(), total=2, api=1, gpu=1)
        await s.start()
        tasks = [submit(s, "run"), submit(s, "run2"), submit(s, "waiting_slot")]
        await settle()
        await s.stop(0.05)
        for t in tasks:
            assert isinstance(t.exception(), ModelNotReady)
        assert s.stats() == EMPTY
    asyncio.run(main())


def test_logs(caplog):  # A9
    async def main():
        fake = Fake()
        fake.gate.set()
        s = Scheduler(fake.runs())
        await s.start()
        await s.submit("taxi_meter", "u")
        await s.stop(1)
    with caplog.at_level(logging.INFO, logger="app"):
        asyncio.run(main())
    assert messages(caplog, "queue job routed") == ["queue job routed: kind=taxi_meter route=api api=1 gpu=0"]
    [done] = messages(caplog, "queue job done")
    assert re.fullmatch(r"queue job done: kind=taxi_meter route=api waited_ms=\d+ ran_ms=\d+", done)


def test_runs_in_caller_context():  # 워커 로그도 넣은 요청의 request_id로 묶인다
    from app.core.logging import request_id

    async def main():
        seen = []

        async def run(url):
            seen.append(request_id.get())
            return {}

        s = Scheduler({"taxi_meter": (run, run)})
        await s.start()

        async def caller(rid):
            request_id.set(rid)
            return await s.submit("taxi_meter", rid)

        await asyncio.gather(caller("req-a"), caller("req-b"))
        await s.stop(1)
        assert sorted(seen) == ["req-a", "req-b"]
        assert request_id.get() is None  # 워커 쪽 값이 새지 않는다

    asyncio.run(main())
