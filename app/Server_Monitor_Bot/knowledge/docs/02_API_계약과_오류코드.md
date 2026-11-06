# API 계약과 오류코드

## 공통 요청 형식

Spring(BE)이 FastAPI의 POST /taxi_meter, POST /receipt를 부를 때의 공통 계약이다(API최종명세-KTB-AIpart.md, 코드로 확인).

- 요청·응답: `Content-Type: application/json`, UTF-8
- 요청 본문 필드는 `image_URL`(string, 필수) 하나다. 대소문자는 `image_URL` 그대로다.
- 정의되지 않은 필드는 무시한다.
- 성공: HTTP 200, `success: true`와 결과 필드
- 실패: HTTP 4xx·5xx, `success: false`, `code`, `message`만 온다. 결과 필드는 없다.

image_URL이 만족해야 하는 조건이다.

| 조건 | 값 |
| --- | --- |
| 접근 | http 또는 https, 5초 안에 리다이렉트 없이 200 |
| 형식 | JPEG 또는 PNG. 확장자가 아니라 파일 내용으로 판단 |
| 크기 | 1바이트 이상, 5MB(5,242,880바이트) 이하 |

## POST /taxi_meter 응답

미터기 사진에서 현재 요금을 읽는다.

요청 예: `{ "image_URL": "https://example.com/meter.jpg" }`

| 필드 | 타입 | 의미 |
| --- | --- | --- |
| success | boolean | true |
| cost | int | 요금. 원 단위 정수 |

응답 예: `{ "success": true, "cost": 3000 }`

- 코드의 응답 모델은 TaxiMeterResponse(success, cost)다.
- 요금을 확정하지 못하면 200 대신 422 JUDGE_PARSE_FAILED를 준다.

## POST /receipt 응답

송금 영수증을 읽는다. 이체 완료로 인정한 경우에만 200을 준다.

| 필드 | 타입 | 의미 |
| --- | --- | --- |
| success | boolean | true. 이체 완료로 인정 |
| amount_cost | int | 송금 금액. 원 단위 정수 |
| transfer_info | string | 화면에 적힌 이체 결과 문구(예: "이체 완료") |
| sender_name | string | 받는 사람 이름 |
| send_time | string 또는 null | 영수증에 적힌 보낸 날짜와 시각. 없으면 null |

- 명세 문서의 응답 표에는 transfer_info가 없지만, 코드의 ReceiptResponse에는 `transfer_info: str`가 있어 응답에 포함된다. 코드 기준이다.
- send_time 예: `"2026-09-23 12:30:45"`, `"2026.09.07 08:15"`, `"2026년 9월 1일 오후 7:42"`. 날짜와 시각 사이 공백만 보정하고 표기는 그대로 둔다.
- 이체 완료로 인정하지 못하면 422 JUDGE_PARSE_FAILED다.

## 오류 응답과 코드표

오류 본문 예: `{"success": false, "code": "IMAGE_FETCH_FAILED", "message": "image download failed"}`. message는 고정 영어다(app/core/exceptions.py).

| HTTP | code | message | 조건 |
| --- | --- | --- | --- |
| 422 | REQUEST_INVALID | request is invalid | JSON이 아니거나 image_URL이 없거나 타입이 아님 |
| 422 | IMAGE_FETCH_FAILED | image download failed | 5초 안에 받지 못함. 연결 실패, 3xx·4xx·5xx, 잘못된 URL 포함 |
| 422 | IMAGE_SIZE_INVALID | image size is invalid | 빈 파일 또는 5MB 초과 |
| 422 | IMAGE_TYPE_INVALID | image must be jpeg or png | JPEG, PNG가 아님 |
| 422 | JUDGE_PARSE_FAILED | model output is not valid | 값을 확정하지 못함. 영수증이 이체 완료가 아닐 때 포함 |
| 502 | UPSTREAM_FAILED | upstream request failed | OpenAI 호출 실패 또는 응답 형식이 다름 |
| 503 | MODEL_NOT_READY | model is not ready | 판독할 수 없음(키 없음 등). 서버 종료 중 받은 요청 포함 |
| 504 | QUEUE_TIMEOUT | queue wait timed out | 내부 큐 대기와 실행을 합쳐 상한 초과 |
| 500 | INTERNAL | internal error | 처리되지 않은 서버 오류 |

- 호스트, 상태 코드, 바이트 수 같은 원인은 응답이 아니라 로그에만 남긴다.

## 요청 추적 헤더(X-Request-ID)

app/core/logging.py의 request_context가 요청마다 request_id를 정한다. 명세 문서에는 없고 코드와 환경변수 안내 문서에 있다.

- Spring이 `X-Request-ID` 헤더를 보내면 그 값을 쓴다. 값은 `^[A-Za-z0-9._-]{1,64}$` 형식이어야 한다.
- 없거나 형식이 맞지 않으면 12자리 16진수 ID를 새로 만든다.
- 응답 헤더 `X-Request-ID`로 같은 값을 돌려준다. 서버 로그 끝의 `request_id=<id>`와 같다.
- 같은 X-Request-ID로 600초 안에 다시 온 요청은 모니터 기록의 attempts(재시도 횟수)로 센다.
- 모의 훈련 요청은 `drill-`로 시작하는 ID를 쓴다.

## 시간 계약

값은 코드 상수와 그 주석 기준이다.

| 항목 | 값 | 위치 |
| --- | --- | --- |
| BE read-timeout | 60초 | inner_queue/scheduler.py DEADLINE |
| 실행 시작 최소 남은 시간 | 30초 | scheduler.py MIN_LEFT |
| 이미지 다운로드 전체 상한 | 5초 | features/*/image.py TIMEOUT |
| OpenAI 구간 상한(재시도 포함) | 45초 | llm/providers/openai.py TIMEOUT |
| Modal 작업 1건 대기 상한 | 15초(플래그 modal.wait_s) | llm/providers/modal_gpu.py WAIT |
| 종료 시 진행 중 작업 대기 | 50초 | main.py scheduler.stop(50), uvicorn --timeout-graceful-shutdown 50 |

- OpenAI 45초는 실측(2026-10-15, 75회) 최대 30.4초의 1.5배다. 주석상 60초 = 이미지 다운로드 최악 10초 + 45초 + 여유 5초로 잡았다.
- Modal 15초는 주석상 60초 - 이미지 다운로드 10초 - luna 30초 - 여유 5초다.
- 큐에서 30초 넘게 기다린 요청은 실행하지 않고 504를 준다(60 - 대기 시간 < 30).
