```python
scheduler = Scheduler(runs={"taxi_meter": (run_api, run_gpu), "receipt": (run_api, run_gpu)})
await scheduler.start()                              # 워커 task 2개(API, GPU) 시작
await scheduler.submit("taxi_meter", image_url)      # 결과 dict. AppError는 그대로 올라온다
scheduler.stats()                                    # {"total", "api", "gpu"} 대기 + 처리 중 개수
await scheduler.stop(timeout=50)                     # 새 작업 거절 → 남은 작업 대기 → 워커 종료
```

이 폴더는 요청을 API 큐와 GPU 큐에 나눠 순서대로 실행만 한다. 브로커와 모델은 모른다.
실행 함수(`run_api`, `run_gpu`)는 기능 폴더의 `main.py`에 있고, 연결은 `app/main.py` lifespan이 한다.
상태는 이벤트 루프 하나에서만 바뀌므로 잠금이 없다. 큐 상한은 프로세스(uvicorn 워커)마다 걸린다.

## 규칙

1. 전체 자리 30(Semaphore). 31번째 `submit`은 자리가 날 때까지 기다린다. 기다린 시간도 기한에 들어간다.
2. API 큐(대기 + 처리 중)가 20 미만이면 API, 아니면 GPU. 30 = 20 + 10이라 자리를 얻으면 반드시 한쪽에 들어간다.
3. 워커는 큐마다 1개, 한 번에 1건씩 실행한다.
4. 꺼낼 때 남은 시간(`60 - 기다린 시간`)이 30초보다 적으면 실행하지 않고 `QueueTimeout`(504). 기다리던 요청이 취소됐으면 건너뛴다.
5. `AppError`는 그대로 돌려주고, 그 밖의 예외는 `InternalError`로 바꾼다. 어느 경우든 자리를 돌려주고 워커는 죽지 않는다.

종료 중(`stop` 이후) `submit`은 `ModelNotReady`(503)로 거절한다. `timeout` 안에 끝나지 않은 작업도 `ModelNotReady`로 끝낸다.

## 상수 (`scheduler.py`)

| 이름 | 값 | 근거 |
| --- | --- | --- |
| `TOTAL` / `API` / `GPU` | 30 / 20 / 10 | 확정 설계. `TOTAL = API + GPU`여야 한다 |
| `DEADLINE` | 60초 | BE read-timeout |
| `MIN_LEFT` | 30초 | luna 실측 최대 30.4초 |

## 로그

| 수준 | 문구 | 필드 |
| --- | --- | --- |
| INFO | `queue job routed` | kind, route, api, gpu (넣은 뒤 큐 개수) |
| WARNING | `queue job expired` | kind, route, waited_ms |
| INFO | `queue job skipped` | kind, route, reason=cancelled |
| INFO | `queue job done` | kind, route, waited_ms, ran_ms |
| ERROR | `unhandled error` | kind, route, error. 스택은 로그에만 남긴다 |
