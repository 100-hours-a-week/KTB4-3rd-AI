# Kafka 연결

AI 서버가 뜰 때 Kafka consumer를 함께 띄워, 요청 토픽을 받아 분석하고 결과 토픽에 낸다.
`KAFKA_BOOTSTRAP_SERVERS`가 비어 있으면 띄우지 않고 HTTP만 동작한다.

## 혼잡도

| 항목 | 값 |
| --- | --- |
| 요청 토픽 | `congestion.analysis.request` (`event_type`: `congestion.analysis.requested`) |
| 결과 토픽 | `congestion.analysis.result` (`event_type`: `congestion.analysis.completed`, key `spot_id`) |
| DLT | `congestion.analysis.request-dlt` |
| consumer 그룹 | `congestion-analyzer` |

메시지는 BE 공통 이벤트 envelope(`event_id` · `event_type` · `schema_version` · `occurred_at` · `data`)로 오간다.
요청은 `data`에 envelope 의 `event_id` · `schema_version`을 합쳐 `CongestionAnalysisMessage`로 읽고,
결과는 분석 결과에서 `event_id` · `schema_version`을 envelope 로 올리고 나머지를 `data`에 넣는다.
`trace_id` 헤더가 오면 로그의 `request_id`로 쓰고 결과에도 붙인다.

| 받은 메시지 | 처리 |
| --- | --- |
| 정상 요청 | 분석 → 성공 결과 발행 |
| 분석 실패 · 시간 초과 | 실패 결과(`FAILED`) 발행 + 원본을 DLT에 기록 |
| `window_end`가 `CONGESTION_STALE_AFTER_MINUTES`보다 오래됨 | 분석하지 않고 실패 결과 발행 |
| envelope 나 요청을 읽을 수 없음, 모르는 `schema_version` | 결과 없이 DLT |
| 다른 `event_type` | 건너뜀 |

## 신뢰성

- 결과와 DLT 발행이 브로커에 저장(`acks=all`)된 뒤에만 오프셋을 커밋한다. 커밋 전에 죽으면 같은 요청을 다시 받는다. 같은 결과가 두 번 가도 BE가 `window_end`로 거른다
- producer는 멱등 전송(`enable_idempotence`)을 켠다
- 한 번에 `CONGESTION_MAX_IN_FLIGHT`건까지 받아 동시에 분석하고, 모두 발행한 뒤 한 번에 커밋한다. 분석 한 건은 `CONGESTION_ANALYSIS_TIMEOUT_SECONDS`를 넘기면 실패 결과로 끝낸다
- 브로커 연결 · 발행 · 커밋이 실패하면 클라이언트를 닫고 5초 뒤 다시 붙어, 커밋한 위치부터 다시 받는다
- 종료 신호를 받으면 새로 받지 않고 하던 묶음을 끝낸 뒤 내려간다. 40초 안에 못 끝내면 끊고, 커밋하지 않은 메시지는 다른 태스크가 받는다
- consumer가 멈추면 `/health`가 `503`을 줘서 ECS가 태스크를 바꾼다
- 여러 태스크를 띄우면 같은 그룹이 파티션을 나눠 갖는다. 요청 토픽 파티션 수가 태스크 수 이상이어야 늘린 만큼 빨라진다

## 환경 변수

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `KAFKA_BOOTSTRAP_SERVERS` | 없음 | 브로커 주소. 없으면 consumer를 띄우지 않는다 |
| `KAFKA_SECURITY_PROTOCOL` | `PLAINTEXT` | `PLAINTEXT` 또는 `SSL`. MSK IAM은 아직 없다 |
| `CONGESTION_ANALYSIS_TIMEOUT_SECONDS` | `240` | 분석 한 건 제한 시간 |
| `CONGESTION_STALE_AFTER_MINUTES` | `60` | 이보다 오래된 구간은 분석하지 않는다 |
| `CONGESTION_MAX_IN_FLIGHT` | `10` | 한 번에 받아 동시에 분석하는 요청 수 |

## 테스트

```bash
pytest -q
KAFKA_TEST_BOOTSTRAP_SERVERS=localhost:9092 pytest -q tests/test_kafka_worker.py
```

두 번째 줄은 실제 브로커로 발행 · DLT · 커밋 · 재시도를 확인한다. 브로커 주소가 없으면 건너뛴다.
