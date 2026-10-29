# 혼잡도 분석

원글·댓글을 받아 **검증 → 관련성 선별 → LLM 판정 → 코드 집계·요약**을 수행한다.
메시지 하나가 스팟 하나다. Spring BE가 이 저장소에서 Python Kafka consumer·producer를
구현하며, 이 폴더는 Kafka 연결 없이 분석 함수를 제공한다.

## consumer에서 부르는 코드

저장소 루트에서 `uv sync`로 의존성을 설치한다. 동기 호출 예시는 아래와 같다.
`raw_request`는 실행 가능한 합성 예시이며, consumer에서는 Kafka 메시지 본문으로 바꾼다.

```python
import json

from pydantic import ValidationError

from app.features.congestion.models import CongestionAnalysisMessage
from app.features.congestion.schema_examples import REQUEST_EXAMPLE
from app.features.congestion.service import (
    LLMCongestionAnalyzer,
    analyze_message,
    failure_result,
)

analyzer = LLMCongestionAnalyzer()  # 한 번 만들어 재사용한다.
raw_request = json.dumps(REQUEST_EXAMPLE, ensure_ascii=False)

try:
    message = CongestionAnalysisMessage.model_validate_json(raw_request)
except ValidationError:
    # consumer에서 원본을 DLT로 보내고, 결과는 발행하지 않는다.
    raise

try:
    result = analyze_message(message, analyzer)
except Exception:
    # 단계별 실패(AppError)와 예상 밖 예외 모두 실패 결과를 발행하고 원본도 기록용 DLT에 남긴다.
    # 원인은 analyze_message가 같은 request_id로 남긴 로그에 있다.
    result = failure_result(message)

result_key = str(result.spot_id)
result_body = result.model_dump_json()
# consumer에서 key·본문을 Kafka 라이브러리 형식으로 인코딩해 발행한다.
# 결과 발행과 필요한 DLT 전송이 성공한 뒤 오프셋을 커밋한다.
```

분석은 동기다. 비동기 consumer에서는 `asyncio`를 import하고 분석 호출을 다음으로 바꾼다.

```python
result = await asyncio.to_thread(analyze_message, message, analyzer)
```

요청 토픽은 `congestion.analysis.request`, 결과 토픽은 `congestion.analysis.result`,
결과 key는 `spot_id`다. 브로커·인증·consumer 그룹·DLT 토픽 이름은 Spring BE·클라우드 팀과
정해야 한다. 위 예시는 Kafka 전송·오프셋 처리를 구현한 코드는 아니다.

## 요청·결과 형식

합성 요청·성공 결과의 기준은 [schema_examples.py](schema_examples.py)의
`REQUEST_EXAMPLE`, `RESPONSE_EXAMPLE`이다. 검증 규칙과 실패 결과는
[models.py](models.py)를 따른다.

| 위치 | 필드와 규칙 |
| --- | --- |
| 요청 | `event_id`: 공백만 아닌 1~128자 문자열, `schema_version`: strict 정수 `1`, `spot_id`: strict 양의 정수 |
| 시각 | `window_end`, `created_at`: 시간대 포함 ISO 8601 문자열. `window_end`는 같은 시각·오프셋으로 반환하며 문자열 모양까지 같지는 않을 수 있음 |
| 묶음 | `post_bundles`: 1~50개. 원글의 `post_id`, `title`(1~30자), `content`(1~280자), `author_key`, `created_at`, `context_only`, `comments` 필수 |
| 댓글 | 묶음당 0~100개. `comment_id`, `content`(1~280자), `author_key`, `created_at` 필수 |
| 공통 제약 | 원글·댓글 ID는 strict 양의 정수, 종류별 ID 중복 금지, 원글·댓글 합계 1,000개 이하. 텍스트는 공백만 불가, `author_key`는 1~128자. 정의되지 않은 필드 거절 |
| 성공 결과 | `analysis_status: SUCCESS`, `result`: `congestion_level`(`LOW`/`MEDIUM`/`HIGH`/`UNKNOWN`), `report_count`, `causes`(`cause_code`·`report_count` 목록), `summary` |
| 결과 공통 | 새 UUID4 `event_id`(요청과 다름), `schema_version: 1`, 요청의 `spot_id`·`window_end`, UTC 완료 시각 `analyzed_at` |

`event_id`는 UUID처럼 영숫자와 `._-`만 쓴 64자 이하 값을 보낸다. 이 형식이 아니면 분석은 하지만
로그에는 `event_id=-`로 남고 `request_id`는 임의 값이 되어, 로그로 요청을 찾기 어렵다.

`context_only`는 필수 strict bool이다. `true`인 원글은 문맥으로만 쓰고 해당 댓글은
판정 후보로 남긴다. 같은 작성자는 원글·댓글에 같은 `author_key`를 보내야 한다.
구간 데이터 선정은 Spring BE가 맡는다. AI는 구간 필터나 미래 작성 시각 검사를 하지 않는다.

## 실패 처리

아래 HTTP 상태는 시험용 HTTP 경로의 상태다. Python consumer는 상태 응답 대신 예외를 받는다.
단계별 예외는 [app/core/exceptions.py](../../core/exceptions.py)의 `AppError`이며,
`code`, `status_code`를 가진다.

| 실패 위치 | 코드 / HTTP 상태 | 뜻 | consumer 재시도 권고 |
| --- | --- | --- | --- |
| 요청 검증 | `REQUEST_INVALID` / 422 | consumer에서는 `ValidationError`. 결과 없이 원본을 DLT로 보냄 | 안 함. 요청 수정 필요 |
| 관련성 선별 | `CONGESTION_DECISIONS_FAILED` / 502 | Decisions 호출 또는 응답 검증 실패 | 일시적 호출 실패일 때만 검토. 응답 형식·ID·확률 오류는 제외 |
| LLM 판정 호출 | `UPSTREAM_FAILED` / 502 | 외부 호출 또는 응답 형식 실패 | 일시적 호출 실패일 때만 검토. 인증·응답 형식 오류는 제외 |
| 모델 준비 | `MODEL_NOT_READY` / 503 | `OPENAI_API_KEY_2`가 없거나 비어 있음 | 키 주입 문제를 해결한 뒤, 다음 구간 전에 끝날 때만 검토 |
| 판정 출력 검증 | `CONGESTION_PARSE_FAILED` / 502 | JSON·값 형식 오류 또는 대상 ID 불일치 | 안 함 |
| 예상 밖 예외 | `INTERNAL` / 500 | HTTP에서 변환되는 코드. consumer에는 원래 예외가 전달됨. 위 예시처럼 실패 결과로 처리 | 안 함. 로그의 스택 트레이스로 원인 확인 |

`analyze_message`는 분석 전체를 재시도하지 않고 예외를 올린다.
실패하면 consumer가 `failure_result(message)`를 호출한다. 예상 밖 예외도 같은 방식으로 처리해,
한 메시지가 consumer를 반복해서 멈추게 하지 않도록 한다.
반환값은 `analysis_status: FAILED`, `result: null`, `code: ANALYSIS_FAILED`,
`message: analysis failed`이며 공통 결과 필드도 포함한다. **실패 결과의 코드는 단계별 코드가
아니다.** 원래 단계별 코드는 같은 요청의 로그에서 확인한다.
실패 결과를 발행하고 원본도 기록용 DLT에 남긴 뒤 오프셋을 커밋한다.
예상 밖 예외는 `analyze_message`가 `code=INTERNAL`과 스택 트레이스를 로그에 남긴 뒤 올린다.

후보가 없거나 모두 무관한 경우는 실패가 아니다. LLM 판정을 생략하고
`UNKNOWN`, `report_count: 0`인 성공 결과를 만든다.

## 재시도와 처리 시간

외부 모델 호출은 [model_client.py](model_client.py)에서만 한다.
429·5xx 응답이면 1초 뒤 한 번 더 보낸다. 시간 초과·연결 실패·그 밖의 4xx·응답 형식
오류는 AI 내부에서 재시도하지 않는다. consumer 재시도는 Spring BE가 결정하되,
위 표의 세 코드(`UPSTREAM_FAILED`, `CONGESTION_DECISIONS_FAILED`, `MODEL_NOT_READY`)에
한정하고 다음 구간 전에 끝낼 수 있을 때만 권한다. 코드만으로 일시적 오류를 구분할 수는
없으므로 같은 요청의 `reason`, `status`, `error` 로그도 확인한다.
실패한 구간을 다음 구간 데이터에 합치지 않는다.

설정은 [config.py](config.py)에 있다. 두 API 모두 `gpt-6-luna`를 사용한다.
현재 값은 실측 전 임시값이다.

| 설정 | timeout | retries | 호출 수 |
| --- | --- | --- | --- |
| `DECISIONS_POLICY` | 30초 | 1 | 후보가 있는 묶음마다 순서대로 1회, 최대 50회 |
| `JUDGE_POLICY` | 60초 | 1 | 선별 후 대상이 있으면 스팟당 1회 |

시도마다 설정된 시간을 모두 쓰고 429·5xx로 재시도하는 경우를 계산하면
`50 × (30 × 2 + 1) + (60 × 2 + 1) = 3,171초`(52분 51초)다.
이는 호출 시간과 재시도 대기로 계산한 값이다. 코드의 `httpx.Client(timeout=...)`는
연결·읽기 등의 제한이며 분석 전체의 마감 시간을 보장하지 않는다. 코드 처리·통신 비용도
별도다. **실측 필요.** 참고로 2026-10-29 Decisions 단독 측정(짧은 입력, 질문 1개)에서
쉬던 뒤 첫 호출은 약 2초, 연달아 부른 호출은 0.25~0.7초였다. 호출 시간은 첫 호출 여부에 따라
크게 달라지므로 실제 요청 형식과 트래픽으로 다시 재서 정한다. `max.poll.interval.ms`는 이 계산과 실측에 여유를 두어 정하고,
한 번의 poll에서 여러 메시지를 순서대로 처리한다면 합산 시간을 고려한다.
비동기 consumer는 위 `asyncio.to_thread` 호출로 이벤트 루프를 막지 않도록 한다.

## 환경 변수·로그·변경 범위

`OPENAI_API_KEY_2`를 consumer 프로세스 환경에 주입한다. 두 API가 같은 키를 사용한다.
이 폴더의 직접 호출은 `.env`를 자동으로 읽지 않는다. 키를 코드·로그·Git에 넣지 않는다.

HTTP 요청 밖에서 `request_id`가 비어 있으면 `analyze_message`가 요청 `event_id`를 넣고
끝나면 복구한다. 이미 값이 있으면 유지한다. 로그는 `congestion analysis started`,
`congestion relevance done`, `congestion judge done`, `congestion aggregate done`,
`congestion analysis done/failed`로 흐름을 확인한다. 모델 시도·토큰·지연은
`congestion model call done/retry/failed`에서 확인한다. Kafka 경로에는 HTTP의
`request done` 로그가 없다. 본문·`author_key`·프롬프트·모델 응답 전문은 로그에 남기지 않는다.

판정 규칙·프롬프트([extractor.py](extractor.py), [spot_extractor.py](spot_extractor.py)),
관련성 선별 기준, 작성자별 최신 표·다수결·동률 처리([aggregator.py](aggregator.py)),
고정 요약([summarizer.py](summarizer.py))을 바꾸려면 AI 담당과 먼저 상의한다.
모델 호출·재시도는 `model_client.py`에 모으며 공용 `app/llm/`을 고치거나 AI 분석 코드에
별도 재시도 루프를 추가하지 않는다. consumer 자체 재시도 여부는 위 인계 기준에 따라 정한다.

[router.py](router.py)의 `POST /congestion-analyses`는 시험용으로 남아 있다.
성공은 결과 메시지, 실패는 단계별 HTTP 오류이며 `200 + FAILED`로 반환하지 않는다.
