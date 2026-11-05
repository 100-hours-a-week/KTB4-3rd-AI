# 혼잡도 분석

원글·댓글을 받아 **검증 → 관련성 선별 → LLM 판정 → 코드 집계·요약**을 수행한다.
메시지 하나가 스팟 하나다. Spring BE가 이 저장소에서 Python Kafka consumer·producer를
구현하며, 이 폴더는 Kafka 연결 없이 분석 함수를 제공한다.

## consumer에서 부르는 코드

저장소 루트에서 `uv sync`로 의존성을 설치한다.
비동기 consumer에서 메시지 하나를 처리하는 예시는 아래와 같다.
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
    result = await analyze_message(message, analyzer)
except Exception:
    # 단계별 실패(AppError)와 예상 밖 예외 모두 실패 결과를 발행하고 원본도 기록용 DLT에 남긴다.
    # 원인은 analyze_message가 같은 request_id로 남긴 로그에 있다.
    result = failure_result(message)

result_key = str(result.spot_id)
result_body = result.model_dump_json()
# consumer에서 key·본문을 Kafka 라이브러리 형식으로 인코딩해 발행한다.
# 결과 발행과 필요한 DLT 전송이 성공한 뒤 오프셋을 커밋한다.
```

위 코드는 비동기 consumer 함수 안에서 실행한다. 동기 consumer의 단독 호출에서는
분석 호출 한 줄을 아래처럼 바꾼다. 이미 이벤트 루프가 실행 중이면 `asyncio.run`을 쓰지 않는다.

```python
import asyncio

result = asyncio.run(analyze_message(message, analyzer))
```

동시 분석은 프로세스마다 하나의 지속되는 이벤트 루프에서 실행한다.
모듈 전역 Semaphore를 여러 스레드·이벤트 루프가 동시에 공유하는 방식은 지원하지 않는다.

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
| 성공 결과 | `analysis_status: SUCCESS`, `result`: `congestion_level`(`LOW`/`MEDIUM`/`HIGH`/`UNKNOWN`), `report_count`, `causes`(`cause_code`·`report_count` 목록), `summary`(필수 키, 문자열 또는 `null`) |
| 결과 공통 | 새 UUID4 `event_id`(요청과 다름), `schema_version: 1`, 요청의 `spot_id`·`window_end`, UTC 완료 시각 `analyzed_at` |

`event_id`는 UUID처럼 영숫자와 `._-`만 쓴 64자 이하 값을 보낸다. 이 형식이 아니면 분석은 하지만
로그에는 `event_id=-`로 남고 `request_id`는 임의 값이 되어, 로그로 요청을 찾기 어렵다.

`context_only`는 필수 strict bool이다. `true`인 원글은 문맥으로만 쓰고 해당 댓글은
판정 후보로 남긴다. 같은 작성자는 원글·댓글에 같은 `author_key`를 보내야 한다.
구간 데이터 선정은 Spring BE가 맡는다. AI는 구간 필터나 미래 작성 시각 검사를 하지 않는다.

`summary`는 코드 집계로 만드는 짧은 상황 문장이다. `UNKNOWN`이면 `null`,
`LOW`이거나 구체 원인이 없으면 "제보한 사람은 N명이에요."다. `MEDIUM`·`HIGH`는
원인별 `report_count`가 가장 큰 원인의 문장을 쓴다.
1위가 동률이면 사고 → 운행 장애 → 지연·정체 → 행사 → 출퇴근 인파 순서로 두 개를 골라
"{A}와 {B} 때문에 혼잡해요."로 잇는다. `HIGH`이면 원인 문장 마지막 어절 앞에 "매우 "를 넣는다.

## 값이 없을 때 규칙

| 필드 | 값이 없을 때 | 뜻 |
| --- | --- | --- |
| `result.congestion_level` | `"UNKNOWN"` | 단계를 판단할 제보가 없음 (enum 값) |
| `result.report_count` | `0` | |
| `result.causes` | `[]` | 원인 제보가 없음. `null`로 보내지 않는다. 단계가 있어도 원인이 없으면 `[]` |
| `result.summary` | `null` | `UNKNOWN`일 때만. 키는 항상 있다 |
| `result` (`FAILED`) | `null` | 실패 결과. 기존 그대로 |

규칙 한 줄: enum은 `UNKNOWN`, 문장은 `null`, 목록은 `[]`.

## 실패 처리

아래 HTTP 상태는 시험용 HTTP 경로의 상태다. Python consumer는 상태 응답 대신 예외를 받는다.
단계별 예외는 [app/core/exceptions.py](../../core/exceptions.py)의 `AppError`이며,
`code`, `status_code`를 가진다.

| 실패 위치 | 코드 / HTTP 상태 | 뜻 | consumer 재시도 권고 |
| --- | --- | --- | --- |
| 요청 검증 | `REQUEST_INVALID` / 422 | consumer에서는 `ValidationError`. 결과 없이 원본을 DLT로 보냄 | 안 함. 요청 수정 필요 |
| 관련성 선별 | 없음 | Decisions 호출·키 준비·응답 검증 실패 시 SemIf 폴백, 그것도 실패하면 키워드 폴백으로 계속 분석 | 안 함 |
| LLM 판정 호출 | `UPSTREAM_FAILED` / 502 | 외부 호출 또는 응답 형식 실패 | 일시적 호출 실패일 때만 검토. 인증·응답 형식 오류는 제외 |
| 판정 모델 준비 | `MODEL_NOT_READY` / 503 | 판정에 쓸 두 모델의 키가 모두 없거나 비어 있음 | 키 주입 문제를 해결한 뒤, 다음 구간 전에 끝날 때만 검토 |
| 판정 출력 검증 | `CONGESTION_PARSE_FAILED` / 502 | JSON·값 형식 오류 또는 대상 ID 불일치 | 안 함 |
| 예상 밖 예외 | `INTERNAL` / 500 | HTTP에서 변환되는 코드. consumer에는 원래 예외가 전달됨. 위 예시처럼 실패 결과로 처리 | 안 함. 로그의 스택 트레이스로 원인 확인 |

관련성 선별 폴백은 `congestion relevance fallback` 경고 로그로 확인한다(`semif=` SemIf 결과, `method=` 실제로 쓴 방식).
순서는 Decisions → SemIf → 키워드다(아래 "SemIf 선별 폴백").
키워드 폴백은 [config.py](config.py)의 `RELEVANCE_KEYWORDS`를 쓴다. 공백을 제거한
원글 제목+본문 또는 댓글 자체 본문에 키워드가 있으면 판정 대상으로 남긴다.
전부 무관하면 `UNKNOWN` 성공 결과를 만든다.

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

외부 모델 호출은 [model_client.py](model_client.py)에서만 한다. 예외: SemIf 폴백은 Modal SDK로 [semif.py](semif.py)에서 부르며 재시도하지 않는다.
429·5xx 응답이면 기본 1초 뒤 한 번 더 보낸다. 429의 `Retry-After`가 유효한 초 값이면
그만큼 기다리며, 정책의 `timeout`보다 길면 기다리지 않고 해당 모델 호출을 실패로 처리한다.
시간 초과·연결 실패·그 밖의 4xx·응답 형식 오류는 같은 모델에 재시도하지 않는다.

판정은 `JUDGE_ENDPOINTS` 순서(OpenAI `gpt-6-luna` → Claude `claude-haiku-5-5`)로 시도한다.
시간 초과·연결 실패·429·5xx·응답 구조 오류·키 없음이면 다음 모델로 넘긴다.
429 외 4xx는 바로 실패하고, 판정 내용 검증 실패(`CONGESTION_PARSE_FAILED`)도
다른 모델로 다시 묻지 않는다. Claude 키가 없으면 Claude 호출을 건너뛰고 앞 모델의 실패를
유지한다. 두 모델 모두 키가 없으면 `MODEL_NOT_READY`다.

consumer 재시도는 Spring BE가 결정하되,
위 표의 두 코드(`UPSTREAM_FAILED`, `MODEL_NOT_READY`)에
한정하고 다음 구간 전에 끝낼 수 있을 때만 권한다. 코드만으로 일시적 오류를 구분할 수는
없으므로 같은 요청의 `reason`, `status`, `error` 로그도 확인한다.
실패한 구간을 다음 구간 데이터에 합치지 않는다.

설정은 [config.py](config.py)에 있다. Decisions는 `DECISIONS_ENDPOINT`의 `gpt-6-luna`를
사용한다. 판정 모델 순서는 `JUDGE_ENDPOINTS`, Claude 출력 한도는 `JUDGE_MAX_TOKENS=8192`다.
현재 호출 정책은 실측 전 임시값이다.

| 설정 | timeout | retries | concurrency | 재시도 제외 호출 수 |
| --- | --- | --- | --- | --- |
| `DECISIONS_POLICY` | 5초 | 1 | 10 | 스팟 전체 후보를 400개씩 나눠 동시 호출, 최대 3회. 실측 95% 3.4초·최대 3.9초(2026-11-05) |
| `SEMIF_WAIT` | 10초 | 0 | Modal 1대 | Decisions 실패 때만. 넘기면 키워드로, 원격 호출은 계속 돌아 컨테이너를 데움 |
| `JUDGE_POLICY` | 60초 | 1 | 5 | 선별 후 대상이 있으면 1회, 폴백 시 다음 모델에 1회 추가 |

9단계부터 원글 묶음마다 최대 50번 부르던 Decisions를 스팟 전체 입력으로 묶는다.
`DECISIONS_MAX_QUESTIONS=400`이며 원글·댓글 합계 한도가 1,000개라 최대 3회다.
Decisions 요청의 `input`은 공통 지시문을 담은 developer 메시지와 스팟 JSON을 담은 user
메시지다. 질문의 `instructions`에는 대상 원글·댓글만 적으며, 나눠 보내는 요청도 같은
`input`을 쓴다.
`concurrency`는 프로세스 하나에서 공유하는 동시 호출 수이며, 판정 한도는 두 모델이 함께 쓴다.
`timeout`은 모델별 시도에 적용되며 판정 폴백 전체의 제한 시간이 아니다.

호출 제한 대기와 재시도 대기가 있으므로 분석 전체의 마감 시간은 별도로 보장하지 않는다.
코드의 `httpx.AsyncClient(timeout=...)`는 연결·읽기 등의 제한이다. 코드 처리·통신 비용도
별도다. **실측 필요.** 스팟 전체 입력·분할 동시 호출·판정 폴백이 적용된 현재 요청 형식과
트래픽으로 처리 시간을 재서 정한다. `max.poll.interval.ms`는 실측에 여유를 두어 정하고,
한 번의 poll에서 여러 메시지를 순서대로 처리한다면 합산 시간을 고려한다.
비동기 consumer는 위 `await analyze_message(...)` 호출로 이벤트 루프를 막지 않도록 한다.

## 환경 변수·로그·변경 범위

`OPENAI_API_KEY_2`를 consumer 프로세스 환경에 주입한다. OpenAI 판정과 Decisions가 같은 키를 쓴다.
Claude 판정 폴백을 쓰려면 `ANTHROPIC_API_KEY`도 주입한다. 없거나 비어 있으면 Claude 호출만
건너뛰며, OpenAI 키가 없어도 키워드 폴백 뒤 Claude로 판정할 수 있다.
이 폴더의 직접 호출은 `.env`를 자동으로 읽지 않는다. 키를 코드·로그·Git에 넣지 않는다.

HTTP 요청 밖에서 `request_id`가 비어 있으면 `analyze_message`가 요청 `event_id`를 넣고
끝나면 복구한다. 이미 값이 있으면 유지한다. 로그는 `congestion analysis started`,
`congestion relevance done`, `congestion judge done`, `congestion aggregate done`,
`congestion analysis done/failed`로 흐름을 확인한다. 모델 시도·토큰·지연은
`congestion model call done/retry/failed`에서 확인한다. 관련성 선별을 대신한 경우는
`congestion relevance fallback`, 판정 모델 전환은 `congestion model fallback` 경고를 확인한다.
`congestion judge done/invalid`의 `model`로 판정 모델을 확인한다. Kafka 경로에는 HTTP의
`request done` 로그가 없다. 본문·`author_key`·프롬프트·모델 응답 전문은 로그에 남기지 않는다.

판정 규칙·프롬프트([extractor.py](extractor.py), [spot_extractor.py](spot_extractor.py)),
관련성 선별 기준, 작성자별 최신 표·다수결·동률 처리([aggregator.py](aggregator.py)),
고정 요약([summarizer.py](summarizer.py))을 바꾸려면 AI 담당과 먼저 상의한다.
모델 호출·재시도는 `model_client.py`에 모으며 공용 `app/llm/`을 고치거나 AI 분석 코드에
별도 재시도 루프를 추가하지 않는다. consumer 자체 재시도 여부는 위 인계 기준에 따라 정한다.

[router.py](router.py)의 `POST /congestion-analyses`는 시험용으로 남아 있다.
성공은 결과 메시지, 실패는 단계별 HTTP 오류이며 `200 + FAILED`로 반환하지 않는다.

## SemIf 선별 폴백 (18단계, D33)

OpenAI Decisions가 실패하면 Modal A10에 올린 SemIf(Qwen3.5-4B + 17단계 LoRA r2)로 선별한다.
평가에서 키워드 폴백보다 놓침·남김이 적었다(F: 결과를 바꾸는 누락 4 → 0, 남긴 무관 28 → 7).
이미지 기능(`app/llm/modal_app.py`)과 같은 방식이다.

| 파일 | 하는 일 |
| --- | --- |
| [modal_app.py](modal_app.py) | Modal 배포 파일. 서버에서 import하지 않는다 |
| [semif.py](semif.py) | SemIf 입력(학습·평가와 같은 형식) 만들기와 원격 호출 |

- 켜기: `MODAL_TOKEN_ID_2`·`MODAL_TOKEN_SECRET_2`(혼잡도 Modal 계정)를 넣고 `.[modal]`로 설치한다. 없으면 SemIf를 건너뛰고 키워드로 간다(v2 기본).
- 배포(`modal_app.py`가 바뀔 때마다):
  `MODAL_TOKEN_ID=$MODAL_TOKEN_ID_2 MODAL_TOKEN_SECRET=$MODAL_TOKEN_SECRET_2 modal deploy -m app.features.congestion.modal_app`
- 가중치는 그 계정의 볼륨 `semif-train`에 있다(기반 모델 캐시 `/hf`, LoRA `/runs/r2/epoch-2`). 지우지 않는다.
- 평소에는 꺼져 있다. 꺼진 상태의 첫 요청은 `SEMIF_WAIT`를 넘겨 키워드로 처리되고, 그동안 컨테이너가 켜진다.
  마지막 요청 뒤 10분이 지나면 다시 꺼진다(`scaledown_window=600`). 과금은 켜져 있는 시간만큼이다.
- OpenAI 복구 확인 로직은 없다. 스팟마다 Decisions를 먼저 부르므로, 돌아오면 다음 스팟부터 Decisions를 쓴다.
- Modal 조회가 한 번 실패하면(토큰·배포 문제) 재시작 전까지 SemIf를 쓰지 않는다(`congestion semif lookup failed` 오류 로그).
- 로그: `congestion semif call done/failed`(후보 수, ms, 실패 이유). 글 내용은 남기지 않는다.

## Spring BE가 알아야 할 변경

### 2026-11-05

- Decisions 제한 시간을 30초에서 5초로 줄였다(재시도 1번은 그대로). 선별 단계의 최악 시간이 짧아진다.
- Decisions가 실패하면 SemIf(GPU) 폴백을 먼저 쓰고, 그것도 실패하면 키워드 폴백을 쓴다. 응답 형식은 그대로다.
- 요약 문장: "출퇴근·등하교하는 사람이 많아요.", "고장·점검 등 운행 장애로 혼잡해요."(원인 코드 이름은 그대로).

### 2026-11-03

- `causes`의 원인 코드 `UNKNOWN`을 삭제했다. 원인이 없으면 `[]`를 보낸다.
- 판정 규칙 정리와 판정·관련성 기준 보완(원인 서술 말투, 사고 직접 제보, 운행 상황 글)으로
  같은 글의 판정이 조금 달라질 수 있다. 응답 형식은 그대로다.
- `cause_code`는 `RUSH_HOUR`, `EVENT`, `ACCIDENT`, `SERVICE_ISSUE`, `DELAY_ISSUE`
  5개다. `ROAD_ISSUE`는 `DELAY_ISSUE`로 바뀐다.
  `BREAKDOWN`·운행 중단은 `SERVICE_ISSUE`,
  `DELAY`·`ROAD_TRAFFIC`·`CONSTRUCTION`·`DEMONSTRATION`·통제·우회는 `DELAY_ISSUE`로 합친다.
  `SERVICE_DISRUPTION`은 운행 중단이면 `SERVICE_ISSUE`, 도로 통제·우회면 `DELAY_ISSUE`다.
  `SERVICE_ISSUE`는 고장·운행 중단만 뜻한다. 그 때문에 늦어지면 이 코드만 쓴다.
  그 밖의 이유나 이유 없이 늦어지면 `DELAY_ISSUE`다. 만석·택시 안 잡힘만으로 원인을 넣지 않는다.
  `WEATHER`는 없다. 원인 코드를 DB enum·상수로 둔 곳도 함께 바꿔야 한다.
- 새 요약 문장은 `SERVICE_ISSUE` "고장이나 운행 중단으로 혼잡해요.",
  `DELAY_ISSUE` "운행이 늦거나 길이 막혀 혼잡해요."다.
- `result.summary`는 필수 키를 유지하면서 `string | null`로 바뀐다. `UNKNOWN`이면
  `null`이므로 Spring BE·FE에서도 허용해야 한다. `FAILED` 결과 형식은 그대로다.
- `LOW`와 원인 없음은 "제보한 사람은 N명이에요.", `MEDIUM`·`HIGH`는
  가장 많은 원인의 고정 문장을 쓴다. 1위 원인 수가 동률이면 1명씩이어도
  사고 → 운행 장애 → 지연·정체 → 행사 → 출퇴근 인파 순서로 앞 두 개를 골라
  "{A}와 {B} 때문에 혼잡해요."로 잇는다.
  `HIGH`는 원인 문장 마지막 어절 앞에 "매우 "를 넣는다. 예: "행사 때문에 사람이 매우 많아요.",
  "지연·정체와 행사 때문에 매우 혼잡해요.". `MEDIUM`과 제보자 수 문장은 그대로다.
- 문장·이름·우선순위는 [summarizer.py](summarizer.py)의 `CAUSE_SENTENCES`,
  `CAUSE_NAMES`, `CAUSE_PRIORITY`에 있다.

### 2026-11-02

- `analyze_message`와 분석기의 `analyze`가 비동기가 됐다. 비동기 consumer는
  `await analyze_message(message, analyzer)`, 동기 consumer의 단독 호출은
  `asyncio.run(analyze_message(message, analyzer))`을 쓴다. 파티션별 동시 처리는
  Spring BE가 구현하며, 프로세스마다 하나의 지속되는 이벤트 루프에서 실행한다.
- Claude 판정 폴백용 환경 변수 `ANTHROPIC_API_KEY`가 추가됐다. 없거나 비어 있으면
  Claude 호출만 건너뛴다. 앞 OpenAI 호출이 실패했다면 그 실패를 유지하며,
  판정 모델의 키가 모두 없을 때는 `MODEL_NOT_READY`다.
- Decisions 호출·키 준비·응답 검증 실패는 키워드 폴백으로 계속 분석한다.
  `CONGESTION_DECISIONS_FAILED`는 삭제됐으므로 consumer 재시도 대상에서도 뺀다.
  `congestion relevance fallback` 경고를 확인하며, 이때 `congestion relevance done`은 남지 않는다.
- 동시 호출 제한은 `config.py`의 `JUDGE_POLICY.concurrency=5`,
  `DECISIONS_POLICY.concurrency=10`이다. 프로세스 전체에서 각각 Semaphore 하나를 공유하며,
  판정 두 모델은 같은 한도를 쓴다. 재시도·판정 폴백 중에도 자리를 유지하고
  취소·실패·완료 시 반환한다. 제한 대기 시간은 `timeout`에 포함되지 않는다.
