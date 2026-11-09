# Modal T4(CUDA)에 OCR과 openjev를 올리는 배포 파일. v3에서 쓴다.
# 배포는 자동이다. 서버가 기동할 때 providers/modal_gpu.py의 prepare()가 APP(코드 버전 포함 이름)을 조회하고, 없으면 배포한다.
# 손으로 배포할 수도 있다: backend-fastapi 폴더에서 `modal deploy -m app.llm.modal_app` (앱 이름은 지금 코드의 APP)
# 가중치는 oj-model-cache 볼륨에 받아 두어, 컨테이너가 새로 떠도 다시 받지 않는다.
import modal

from ..features.receipt import pipeline as receipt
from ..features.taxi_meter import pipeline as taxi_meter
from . import router
from .providers.modal_gpu import APP, CLS
from .providers.ocr import OcrEngine
from .providers.slm import SlmEngine
from .types import ImageBytes

# GPU 메모리 스냅샷은 Modal 알파 기능이다. 실험할 때만 True로 켠다(켜면 snap=True 단계에서 바로 GPU에 올린다).
GPU_SNAPSHOT = False
# 작업 종류 → 그 기능의 model_pipe_run(image, device)
PIPES = {"taxi_meter": taxi_meter.model_pipe_run, "receipt": receipt.model_pipe_run}

cache = modal.Volume.from_name("oj-model-cache", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0")
    .pip_install(
        "paddlepaddle-gpu==3.3.1",
        index_url="https://www.paddlepaddle.org.cn/packages/stable/cu126/",
    )
    .pip_install("paddleocr==3.7.0", "torch>=2.13", "transformers>=5.16", "pillow>=12", "httpx>=0.28")
    .env(
        {
            "HF_HOME": "/cache/hf",
            "PADDLE_PDX_CACHE_HOME": "/cache/paddlex",
            "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
        }
    )
    .add_local_python_source("app")
)

app = modal.App(APP, image=image, include_source=False)


# 과금은 컨테이너가 떠 있는 시간(기동 + 추론 + 유휴 대기)이다. 모델을 내리는 것은 컨테이너 종료로 한다.
# scaledown_window: 마지막 요청 뒤 60초 동안 다음 요청이 없으면 종료한다. 늘리면 cold start가 줄고 유휴 과금이 는다.
# max_containers=1: 동시 과금 상한. 밀린 요청은 modal_gpu.WAIT를 넘기면 luna로 넘어간다.
# timeout=120: 작업 1건 실행 상한. 멈춘 작업이 오래 과금되지 않게 한다.
# enable_memory_snapshot: CPU 메모리 스냅샷. openjev를 CPU에 올린 상태를 저장해 두고 cold start 때 복원한다.
# ponytail: memory=16384는 fp16 가중치 약 8GB 기준 추정이다. 실측(최고 사용량) 뒤 줄인다.
@app.cls(
    gpu="T4",
    volumes={"/cache": cache},
    memory=16384,
    min_containers=0,
    max_containers=1,
    scaledown_window=60,
    timeout=120,
    enable_memory_snapshot=True,
    experimental_options={"enable_gpu_snapshot": True} if GPU_SNAPSHOT else None,
)
class OjModels:
    @modal.enter(snap=True)
    def load(self) -> None:
        # 스냅샷에 담기는 단계. GPU가 없으므로 cpu에 fp16으로 올린다. GPU 스냅샷이면 바로 cuda에 올린다.
        self.slm = SlmEngine("cuda")
        self.slm.load(place="cuda" if GPU_SNAPSHOT else "cpu")
        cache.commit()

    @modal.enter(snap=False)
    def start(self) -> None:
        # 복원 뒤 단계. openjev를 GPU로 옮기고, Paddle OCR은 GPU에서 새로 초기화한다.
        self.slm.to("cuda")
        ocr = OcrEngine("cuda")
        ocr.load()
        # 파이프라인의 llm(..., device="cuda")가 이 엔진을 쓰게 등록한다.
        router.engines[("slm", "cuda")] = self.slm
        router.engines[("ocr", "cuda")] = ocr
        cache.commit()

    @modal.method()
    async def pipe(self, kind: str, content: bytes, media_type: str) -> dict | None:
        image = ImageBytes(content, 0, 0, media_type, "")
        return await PIPES[kind](image, "cuda")
