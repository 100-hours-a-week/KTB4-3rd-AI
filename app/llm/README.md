```python
llm("ocr", image=image, device="cpu")          # 검출 + 인식으로 읽은 원문 str
llm("slm", [(원문, 가설), ...], device="cpu")   # 가설마다 [모순, 지지, 중립]
llm("api_1", prompt, image)                    # 이미지용 luna, JSON 문자열
llm("api_2", prompt, image)                    # 혼잡도용 luna, JSON 문자열
load("ocr", device="cpu")                      # 기동 때 미리 올리기
```

이 폴더는 모델을 한 번 돌리고 결과만 돌려준다. 같은 모델과 device 조합은 한 번만 올린다.
요청 순서 제어와 파이프라인 구성은 기능 폴더의 `main.py`가 맡는다.
후보 추출(`core/oj_candidates.py`)과 openjev 판정 규칙(`core/oj_decide.py`)은 `core`에 있다.

## 실행 환경

`core/config.py`가 env로 이미지 모델(OCR, openjev)을 어디서 돌릴지 정한다. 값은 `.env.example`을 따른다.
정하는 순서는 ① `MODAL_TOKEN_ID_1`·`MODAL_TOKEN_SECRET_1`이 있으면 Modal T4, ② `LOCAL_DEVICE=TRUE`면 이 서버(cuda, mps, cpu 순), ③ 아니면 luna만이다.
기동 때 모델 로드나 Modal 조회에 실패하면 그 장치는 재시작 전까지 쓰지 않고 바로 luna로 처리한다.
`_1`은 이미지(택시미터, 영수증), `_2`는 혼잡도용이다. 키와 Modal 계정을 서로 나눈다.

| 환경 | env | 설치 | 동작 |
| --- | --- | --- | --- |
| v2 | `LOCAL_DEVICE=FALSE`, Modal 키 없음 | 기본 | 항상 luna |
| 로컬 테스트 | `LOCAL_DEVICE=TRUE` | `.[local]` | 이 서버에서 OCR, openjev |
| v3 | `MODAL_TOKEN_ID_1`, `MODAL_TOKEN_SECRET_1` | `.[modal]` | Modal T4에서 OCR, openjev |

```bash
uvicorn app.main:app --env-file .env   # 서버 실행
# v3: Modal T4에 모델 배포 (_1 계정으로, 모델 코드가 바뀔 때마다)
MODAL_TOKEN_ID=$MODAL_TOKEN_ID_1 MODAL_TOKEN_SECRET=$MODAL_TOKEN_SECRET_1 modal deploy -m app.llm.modal_app
```
