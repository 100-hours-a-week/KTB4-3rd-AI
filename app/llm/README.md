```python
llm("ocr", image=image, device="cpu")          # 검출 + 인식으로 읽은 원문 str
llm("slm", [(원문, 가설), ...], device="cpu")   # 가설마다 [모순, 지지, 중립]
llm("api", prompt, image)                      # luna, JSON 문자열
load("ocr", device="cpu")                      # 기동 때 미리 올리기
```

이 폴더는 모델을 한 번 돌리고 결과만 돌려준다. 같은 모델과 device 조합은 한 번만 올린다.
요청 순서 제어와 파이프라인 구성은 기능 폴더의 `main.py`가 맡는다.
후보 추출(`core/oj_candidates.py`)과 openjev 판정 규칙(`core/oj_decide.py`)은 `core`에 있다.

## 실행 환경

`IMAGE_DEVICE`(`core/config.py`)로 이미지 모델을 어디서 돌릴지 정한다. 값은 `.env.example`을 따른다.

| 환경 | IMAGE_DEVICE | 설치 | 동작 |
| --- | --- | --- | --- |
| 로컬 테스트 | `mps` 또는 `cpu` | `.[local]` | 이 서버에서 OCR, openjev |
| v2 | 비움(`off`) | 기본 | 항상 luna |
| v3 | 비움 + Modal 키, 또는 `modal` | `.[modal]` | Modal T4에서 OCR, openjev |

```bash
uvicorn app.main:app --env-file .env   # 서버 실행
modal deploy -m app.llm.modal_app      # v3: Modal T4에 모델 배포 (한 번, 모델 코드가 바뀔 때마다)
```
