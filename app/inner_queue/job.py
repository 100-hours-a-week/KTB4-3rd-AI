# 큐에 들어가는 작업 하나. 외부 큐(Kafka)가 붙어도 같은 형식을 쓴다.
import asyncio
import contextvars
from dataclasses import dataclass, field


@dataclass
class Job:
    kind: str                # "taxi_meter" | "receipt"
    image_url: str
    received: float          # loop.time(), 받은 시각
    future: asyncio.Future   # 결과 dict 또는 AppError
    route: str = ""          # "api" | "gpu", 배분 때 정함
    # 넣은 쪽의 contextvars(request_id 등). 워커가 이 안에서 실행해 로그가 요청과 묶인다.
    context: contextvars.Context = field(default_factory=contextvars.copy_context)
