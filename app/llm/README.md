```python
await llm("ocr", image=image, device="cpu")          # 검출 + 인식으로 읽은 원문 str (스레드에서 돈다)
await llm("slm", [(원문, 가설), ...], device="cpu")   # 가설마다 [모순, 지지, 중립] (스레드에서 돈다)
await llm("api_1", prompt, image)                    # 이미지용 luna, JSON 문자열 (비동기 HTTP)
await llm("api_2", prompt, image)                    # 혼잡도용 luna, JSON 문자열 (비동기 HTTP)
await pipe("taxi_meter", image)                      # Modal T4에서 작업 1건(OCR + openjev)을 한 번에. 결과 dict 또는 None
await prepare_modal()                                # 기동 때 한 번. Modal 앱 조회, 없으면 배포
load("ocr", device="cpu")                            # 로컬 장치 미리 올리기(동기, 기동 때 스레드에서)
```

이 폴더는 모델을 한 번 돌리고 결과만 돌려준다. 같은 모델과 device 조합은 한 번만 올린다.
요청 순서 제어와 파이프라인 구성은 기능 폴더의 `main.py`가 맡는다.
후보 추출(`core/oj_candidates.py`)과 openjev 판정 규칙(`core/oj_decide.py`)은 `core`에 있다.

## 실행 환경

`core/config.py`가 env로 이미지 모델(OCR, openjev)을 어디서 돌릴지 정한다. 값은 `.env.example`을 따른다.
정하는 순서는 ① `MODAL_TOKEN_ID_1`·`MODAL_TOKEN_SECRET_1`이 있으면 Modal T4, ② `LOCAL_DEVICE=TRUE`면 이 서버(cuda, mps, cpu 순), ③ 아니면 luna만이다.
로컬 장치는 기동 때 모델 로드에 실패하면 재시작 전까지 쓰지 않고(`down`) 바로 luna로 처리한다.
`_1`은 이미지(택시미터, 영수증), `_2`는 혼잡도용이다. 키와 Modal 계정을 서로 나눈다.

| 환경 | env | 설치 | 동작 |
| --- | --- | --- | --- |
| v2 | `LOCAL_DEVICE=FALSE`, Modal 키 없음 | 기본 | 항상 luna |
| 로컬 테스트 | `LOCAL_DEVICE=TRUE` | `.[local]` | 이 서버에서 OCR, openjev |
| v3 | `MODAL_TOKEN_ID_1`, `MODAL_TOKEN_SECRET_1` | 기본 | Modal T4에서 `pipe` |

## Modal (v3)

- 키만 넣으면 된다. 기동 때 `prepare_modal()`이 앱 `oj-image-models-<VERSION>`을 조회하고, 없으면 그 자리에서 배포한다. 기동은 막지 않는다.
- `VERSION`은 `llm`, `features/taxi_meter`, `features/receipt`, `core` 아래 `.py` 내용의 sha256 앞 12자다. 코드가 바뀌면 새 이름으로 배포되어 서버와 원격 코드가 어긋나지 않는다. 예전 버전 앱은 Modal 대시보드에서 지운다.
- 상태: `idle → deploying → ready`, 실패하면 `down`(재시작 전까지). `ready`가 아니면 `pipe`는 기다리지 않고 `ModelNotReady` → luna. 첫 배포(이미지 빌드)는 몇 분 걸린다.
- `pipe`는 작업당 원격 호출 1번이다. 15초(`WAIT`) 안에 답이 없거나 실패하면 `UpstreamError` → luna. 원격 호출은 계속 돌아 컨테이너를 데운다.
- 스냅샷: `@modal.enter(snap=True)`에서 openjev를 CPU에 fp16으로 올려 CPU 메모리 스냅샷에 담고, 복원 뒤(`snap=False`) GPU로 옮기고 Paddle OCR을 GPU에서 초기화한다. GPU 스냅샷은 `modal_app.GPU_SNAPSHOT`(기본 `False`)으로 실험할 때만 켠다.
- 비용 설정(`modal_app.py`): T4, 컨테이너 0~1개, 유휴 60초 뒤 종료, 작업 1건 실행 상한 120초, 메모리 16GB(실측 뒤 조정). 가중치는 `oj-model-cache` 볼륨에 둔다.

| 로그 | 뜻 |
| --- | --- |
| `modal state changed: from= to=` | 상태가 바뀔 때만 |
| `modal deploy started/done: app= (ms=)` | 자동 배포 시작·끝 |
| `modal deploy failed: app= reason= hint=` | 배포 실패 → `down` |
| `modal lookup failed: reason= slot= hint=` | 토큰 없음·틀림 등 → `down` |
| `modal call failed: method=pipe reason=timeout\|auth\|not_found\|quota\|permission\|remote_error` | 작업 1건 실패 → luna |

```bash
uvicorn app.main:app --env-file .env   # 서버 실행 (v3면 기동 때 자동 배포)
# 손으로 배포할 때(_1 계정으로). 앱 이름은 지금 코드의 APP이다.
MODAL_TOKEN_ID=$MODAL_TOKEN_ID_1 MODAL_TOKEN_SECRET=$MODAL_TOKEN_SECRET_1 modal deploy -m app.llm.modal_app
```
