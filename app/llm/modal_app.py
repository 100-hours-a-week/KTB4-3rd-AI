# Modal T4(CUDA)에 OCR과 openjev를 올리는 배포 파일. v3에서 쓴다.
# 배포: backend-fastapi 폴더에서 `modal deploy -m app.llm.modal_app`
# 가중치는 oj-model-cache 볼륨에 받아 두어, 컨테이너가 새로 떠도 다시 받지 않는다.
import modal

from .providers.modal_gpu import APP, CLS
from .providers.ocr import OcrEngine
from .providers.slm import SlmEngine
from .types import ImageBytes

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


@app.cls(gpu="T4", volumes={"/cache": cache}, scaledown_window=300, timeout=600)
class OjModels:
    @modal.enter()
    def start(self) -> None:
        self.ocr = OcrEngine("cuda")
        self.slm = SlmEngine("cuda")
        self.ocr.load()
        self.slm.load()

    @modal.method()
    def read(self, content: bytes, media_type: str) -> str:
        return self.ocr.read(ImageBytes(content, 0, 0, media_type, ""))

    @modal.method()
    def predict(self, pairs: list[tuple[str, str]]) -> list[list[float]]:
        return self.slm.predict(pairs)
