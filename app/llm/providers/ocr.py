# OCR 인터페이스. 검출 모델과 인식 모델을 한 파이프라인으로 묶어 이미지의 글자를 읽는다.
import re
import tempfile
import threading
from typing import Any, TypedDict

from ...core.exceptions import InternalError, ModelNotReady
from ...core.logging import get_logger
from ..types import ImageBytes

logger = get_logger(__name__)

MIN_SCORE = 0.6
DET = "PP-OCRv6_medium_det"
REC = "korean_PP-OCRv5_mobile_rec"
DIGIT_WORD = re.compile(r"\S*\d\S*")


class OcrPage(TypedDict):
    rec_texts: list[str]
    rec_scores: list[float]


def page_lines(page: OcrPage) -> list[str]:
    lines = []
    for text, score in zip(page["rec_texts"], page["rec_scores"]):
        text = text.strip()
        if score < MIN_SCORE or not text:
            continue
        # 인식기가 숫자 0을 O로 읽는 경우가 많아, 숫자가 섞인 단어에서만 바로잡는다.
        text = DIGIT_WORD.sub(lambda m: m.group().replace("O", "0").replace("o", "0"), text)
        lines.append(text)
    return lines


class OcrEngine:
    def __init__(self, device: str) -> None:
        self.device = {"cuda": "gpu:0", "mps": "cpu"}.get(device, device)
        self.pipeline = None
        # 로드만 잠근다. 추론 순서는 호출하는 쪽에서 정한다.
        self.lock = threading.RLock()

    def read(self, image: ImageBytes) -> str:
        suffix = ".png" if image.media_type == "image/png" else ".jpg"
        with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
            tmp.write(image.content)
            tmp.flush()
            pipeline = self.load()
            try:
                pages = pipeline.predict(tmp.name)
            except Exception as exc:
                logger.exception("ocr inference failed")
                raise InternalError() from exc
        return "\n".join(line for page in pages for line in page_lines(page))

    def load(self) -> Any:
        with self.lock:
            if self.pipeline is not None:
                return self.pipeline
            try:
                from paddleocr import PaddleOCR
            except ImportError as exc:
                logger.exception("paddleocr is not installed")
                raise ModelNotReady("ocr model is not ready") from exc
            try:
                self.pipeline = PaddleOCR(
                    text_detection_model_name=DET,
                    text_recognition_model_name=REC,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    # 줄 방향 모델을 켜면 똑바로 찍힌 줄도 180도 뒤집어 읽는다.
                    use_textline_orientation=False,
                    device=self.device,
                )
            except Exception as exc:
                logger.exception("ocr model load failed")
                raise ModelNotReady("ocr model is not ready") from exc
            return self.pipeline
