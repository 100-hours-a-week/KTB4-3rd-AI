# GPU 경로

## GPU 경로가 쓰이는 조건

GPU 경로(V3)는 Modal L4에서 OCR과 openjev를 돌리는 경로다.

- 서비스 DEVICE가 modal이어야 한다: MODAL_TOKEN_ID_1과 MODAL_TOKEN_SECRET_1이 둘 다 있을 때.
- 내부 큐 정책상 평소 요청은 API 큐(luna)로 간다. API 큐에 20건 이상 있을 때만 새 요청이 GPU 큐로 간다(inner_queue/scheduler.py).
- GPU 큐 요청도 route.force_luna 플래그가 켜져 있거나 Modal 상태가 ready가 아니면 바로 luna로 처리한다.
- GPU 결과가 실제로 쓰이려면 요청이 GPU 큐로 가고, 컨테이너가 살아 있는 동안 오며, 판정이 보류(review)가 아니어야 한다.

요청 1건의 경로:
POST /receipt·/taxi_meter → Scheduler.submit() → features/*/main.py run_gpu()(이미지 다운로드·검사) → llm/router.pipe() → ModalEngine.pipe()(대기 상한 modal.wait_s, 기본 15초) → 원격 OjModels.pipe()(OCR + openjev 판정) → 결과 dict 또는 None. None·실패·시간 초과면 luna로 대체.

## Modal 앱 구조(app/llm/modal_app.py)

| 항목 | 값 |
| --- | --- |
| 앱 이름 | `oj-image-models-<코드 해시 12자>` |
| 클래스·메서드 | OjModels.pipe(kind, content, media_type) |
| GPU | L4 |
| memory | 16384(MB) |
| min_containers / max_containers | 0 / 1 |
| scaledown_window | 60초 |
| timeout | 120초(작업 1건 실행 상한) |
| 스냅샷 | enable_memory_snapshot + enable_gpu_snapshot(GPU_SNAPSHOT = True) |
| 볼륨 | `oj-model-cache`를 `/cache`에 연결(HF·PaddleX 캐시 경로) |
| 이미지 | debian_slim Python 3.12, paddlepaddle-gpu 3.3.1(cu126), paddleocr 3.7.0, torch, transformers |

- 과금은 컨테이너가 떠 있는 시간(기동 + 추론 + 유휴 대기)이다. 모델을 내리는 것은 컨테이너 종료로 한다.
- scaledown_window 60초: 마지막 요청 뒤 60초 동안 다음 요청이 없으면 컨테이너가 종료된다.
- max_containers=1: 동시 과금 상한. 밀린 요청은 대기 상한을 넘기면 luna로 넘어간다.
- 상시 켜 두기(min_containers 등)는 상시 과금 때문에 쓰지 않는다(2026-10-27 결정).

## 원격 모델 로드와 GPU 스냅샷

- `@modal.enter(snap=True)` 단계에서 openjev(fp16)와 Paddle OCR을 처음부터 GPU(cuda)에 올리고, 파이프라인이 쓰도록 router.engines에 등록한다.
- GPU 메모리 스냅샷(Modal 알파 기능)이 이 GPU 상태를 저장해 두고 cold start 때 복원한다. 모델을 GPU로 옮기는 별도 단계가 없다.
- 가중치는 Volume 캐시(`/cache`)에서 읽어, 컨테이너가 새로 떠도 다시 받지 않는다.
- GPU_SNAPSHOT=False 설정은 스냅샷을 끈다. 이때도 enter(snap=True)는 기동 때 실행되어 모델이 GPU에 바로 로드된다(03_적용_내역의 대체안).
- Modal 문서 기준: 스냅샷은 배포된 앱에서만 만들어지고, GPU 함수는 GPU 종류당 처음 몇 번 기동하며 2~3개를 만든다. 코드나 GPU 종류가 바뀌면 이전 스냅샷은 무효다.

## 버전 이름과 자동 배포

app/llm/providers/modal_gpu.py가 서버 기동 때 원격 앱을 준비한다.

- VERSION은 원격이 실제로 import하는 모듈 20개(REMOTE_MODULES) 파일의 (상대 경로 + 내용)을 정렬해 만든 sha256 앞 12자다. 이 파일들이 바뀔 때만 앱 이름이 바뀌어 새로 배포된다.
- 원격 import 목록이 늘면 테스트(test_remote_modules_match_modal_app_imports)가 실패해 목록 갱신을 강제한다.
- 상태 변화: idle → deploying → ready, 실패하면 down. down은 재시작 전까지 고정이다.
- prepare(): 토큰으로 Modal 클라이언트를 만들고 지금 버전 이름의 앱을 조회한다. 있으면 ready.
- 앱이 없으면(NotFoundError) 그 자리에서 배포한다. 첫 빌드는 몇 분 걸리고, 그동안 GPU 경로 요청은 바로 luna로 넘어간다.
- 조회가 다른 이유로 실패하면(토큰 없음·틀림 등) down.
- 수동 배포: backend-fastapi 폴더에서 `modal deploy -m app.llm.modal_app`

## 예열(warm-up)

- 자동 배포에 성공한 직후에만 예열을 1회 한다. 이미 있는 앱을 조회한 경우에는 예열하지 않는다.
- 예열 입력: 64×64 흰색 JPEG로 원격 pipe("taxi_meter")를 1회 호출한다. 시간 제한 없음, 결과는 버린다.
- 예열 중 상태는 deploying이라 그사이 요청은 luna가 처리한다. 끝나면 ready.
- 로그: `modal warmup done: app, ms` 또는 `modal warmup failed: app, error`.
- 목적: 배포 직후 가장 느린 첫 기동(스냅샷 생성)을 사용자 요청 대신 예열이 겪게 한다. 배포마다 GPU 약 1분을 쓴다.

## cold start와 대기 상한

- Modal 작업 1건 대기 상한은 15초(WAIT, 플래그 modal.wait_s로 10~30초 조정)다. 주석상 BE 60초 - 이미지 다운로드 10초 - luna 30초 - 여유 5초.
- 측정된 최선의 cold start(22.0초)도 15초보다 길다. 그래서 쉬던 뒤 첫 GPU 요청은 설계대로 luna로 넘어간다.
- 대기 상한을 넘겨도 원격 호출은 취소되지 않고 계속 돌아 컨테이너를 데운다. 다음 요청이 60초 안에 오면 warm 상태에서 처리된다.
- L4 수급 대기가 길면 그동안 GPU 큐 요청은 15초를 기다린 뒤 luna로 넘어가, 응답은 나가지만 요청마다 약 15초가 늘어난다.

## 측정 결과(2026-10-27)

샌드박스 Docker의 앱 컨테이너에서 원격 pipe를 직접 호출해 잰 값이다. 입력은 택시 미터기 사진 1장(648,080바이트). cold는 직전 호출 뒤 95초 이상 기다린 상태다.

| 항목 | 최적화 전(T4, CPU 스냅샷) | 최적화 후(L4, GPU 스냅샷) |
| --- | --- | --- |
| 배포 | 616.9초(이미지 빌드 포함) | 4.3초(레이어 재사용) |
| 배포 직후 첫 호출 | 188.1초(스냅샷 생성) | 자동 예열 59.6초(사용자 요청 아님) |
| cold start | 46.1초, 38.8초 | 1,400.5초(L4 수급 대기), 22.0초, 128.2초 |
| warm | 0.9초 | 0.9초 |

- 모든 GPU 결과가 판정 보류(`judge fallback: status=review`)였다. 운영에서는 luna로 대체된다.
- GPU 종류와 스냅샷 방식이 함께 바뀐 비교이고 표본이 적다. 128.2초 편차의 원인은 확정되지 않았다.

## 남은 결정과 후보

05_남은_과제 문서 기준, 아직 정하지 않았거나 적용하지 않은 항목이다.

| 구분 | 항목 |
| --- | --- |
| 결정 필요 D1 | L4 수급 대기 대응: L4 단일 유지 / GPU 대체 목록(예: L4 → A10G) / T4로 되돌림 |
| 결정 필요 D2 | GPU 사용 정책: API 큐 20건 이상일 때만 GPU(현재) / 평소에도 GPU 우선 |
| 결정 필요 D3 | modal.wait_s 기본 15초 유지 또는 조정 |
| 후보 N1 | 이미지에 causal-conv1d, flash-linear-attention 설치(현재 느린 reference 구현으로 대체 중) |
| 후보 N2 | HF_HUB_OFFLINE=1(로드 때 huggingface.co 조회 제거) |
| 후보 N3 | cold start 표본 확대(10회 이상) |
| 후보 N4 | memory=16384 실측 후 조정 |
| 후보 N5 | 토큰 값 앞뒤 공백 제거를 코드에서 처리 |

- GPU 판정 통과율은 평가셋으로 측정해야 한다. 판정 임계치: PASS_ENTAILMENT 0.75, REJECT_CONTRADICTION 0.60, MARGIN 0.15(core/oj_decide.py).
