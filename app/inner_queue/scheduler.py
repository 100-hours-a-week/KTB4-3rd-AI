# 요청을 API 큐와 GPU 큐에 나눠 담고, 큐마다 워커 하나가 한 건씩 실행한다.
# 브로커와 모델은 모른다. 실행 함수(run_api, run_gpu)는 kind마다 밖에서 받는다.
# 상태는 이벤트 루프 하나에서만 바뀌므로 잠금이 없다.
# 큐 개수(count)에는 대기 중과 처리 중 작업이 모두 들어간다.
import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping

from ..core.exceptions import AppError, InternalError, ModelNotReady, QueueTimeout
from ..core.logging import get_logger
from .job import Job

logger = get_logger(__name__)

Runner = Callable[[str], Awaitable[dict]]  # image_url -> 응답 dict

TOTAL = 30        # 전체 자리
API = 20          # API 큐 상한
GPU = 10          # GPU 큐 상한 (= TOTAL - API)
DEADLINE = 60.0   # BE read-timeout
MIN_LEFT = 30.0   # luna 실측 최대 30.4초. 이보다 적게 남으면 시작하지 않는다


class Scheduler:
    def __init__(self, runs: Mapping[str, tuple[Runner, Runner]], *, total: int = TOTAL, api: int = API,
                 gpu: int = GPU, deadline: float = DEADLINE, min_left: float = MIN_LEFT) -> None:
        # 전체 자리를 얻으면 반드시 한쪽 큐에 들어가야 하므로 total = api + gpu여야 한다.
        if total != api + gpu:
            raise ValueError("total must equal api + gpu")
        self.runs = runs
        self.api = api
        self.deadline = deadline
        self.min_left = min_left
        self.slots = asyncio.Semaphore(total)
        self.queues = {"api": asyncio.Queue(), "gpu": asyncio.Queue()}
        self.count = {"api": 0, "gpu": 0}
        self.idle = asyncio.Event()  # 큐 개수가 모두 0이면 켜진다
        self.idle.set()
        self.stopping = False
        self.workers: list[asyncio.Task] = []

    async def start(self) -> None:
        self.workers = [asyncio.create_task(self.work(route)) for route in self.queues]

    async def stop(self, timeout: float) -> None:
        # 새 작업 거절 → 남은 작업이 끝나길 timeout까지 기다림 → 워커 취소 → 못 한 작업은 ModelNotReady
        self.stopping = True
        with contextlib.suppress(asyncio.TimeoutError):  # 3.10에서는 TimeoutError와 다른 클래스다
            await asyncio.wait_for(self.idle.wait(), timeout)
        for task in self.workers:
            task.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        for q in self.queues.values():
            while not q.empty():
                self.finish(q.get_nowait())

    async def submit(self, kind: str, image_url: str) -> dict:
        if self.stopping:
            raise ModelNotReady()
        loop = asyncio.get_running_loop()
        received = loop.time()  # 자리를 기다린 시간도 기한에 넣는다
        await self.slots.acquire()
        if self.stopping:  # 자리를 기다리는 사이 종료가 시작됨
            self.slots.release()
            raise ModelNotReady()
        route = "api" if self.count["api"] < self.api else "gpu"
        job = Job(kind, image_url, received, loop.create_future(), route)
        self.count[route] += 1
        self.idle.clear()
        self.queues[route].put_nowait(job)
        logger.info("queue job routed: kind=%s route=%s api=%d gpu=%d",
                    kind, route, self.count["api"], self.count["gpu"])
        # 기다리던 쪽이 취소되면 asyncio가 이 future도 취소한다. 워커는 꺼낼 때 보고 건너뛴다.
        return await job.future

    def stats(self) -> dict[str, int]:
        return {"total": self.count["api"] + self.count["gpu"], **self.count}

    async def work(self, route: str) -> None:
        q = self.queues[route]
        while True:
            job = await q.get()
            # 워커 task는 기동 때 만들어져 요청의 request_id가 없다. 작업을 넣은 쪽의 context로 실행한다.
            # 워커가 취소되면 이 task도 취소되고, finish까지 마친 뒤 워커가 끝난다.
            await job.context.run(asyncio.ensure_future, self.run(job))

    async def run(self, job: Job) -> None:
        try:
            await self.handle(job)
        finally:
            self.finish(job)

    async def handle(self, job: Job) -> None:
        loop = asyncio.get_running_loop()
        start = loop.time()
        waited_ms = int((start - job.received) * 1000)
        if job.future.cancelled():
            logger.info("queue job skipped: kind=%s route=%s reason=cancelled", job.kind, job.route)
            return
        if self.deadline - (start - job.received) < self.min_left:
            logger.warning("queue job expired: kind=%s route=%s waited_ms=%d", job.kind, job.route, waited_ms)
            job.future.set_exception(QueueTimeout())
            return
        result, error = None, None
        try:
            run_api, run_gpu = self.runs[job.kind]
            result = await (run_api if job.route == "api" else run_gpu)(job.image_url)
        except AppError as exc:
            error = exc
        except Exception as exc:
            logger.exception("unhandled error: kind=%s route=%s error=%s", job.kind, job.route, type(exc).__name__)
            error = InternalError()
        ran_ms = int((loop.time() - start) * 1000)
        logger.info("queue job done: kind=%s route=%s waited_ms=%d ran_ms=%d", job.kind, job.route, waited_ms, ran_ms)
        if job.future.done():  # 실행 중 호출자가 취소됨
            return
        if error:
            job.future.set_exception(error)
        else:
            job.future.set_result(result)

    def finish(self, job: Job) -> None:
        # 성공·실패·건너뜀·시간 초과·종료 모두 여기서 자리를 돌려준다.
        if not job.future.done():  # 종료로 실행하지 못했거나 실행 중 워커가 취소됨
            job.future.set_exception(ModelNotReady())
        self.count[job.route] -= 1
        self.slots.release()
        if not any(self.count.values()):
            self.idle.set()
