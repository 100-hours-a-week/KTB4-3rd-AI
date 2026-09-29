# openjev 4B 인터페이스
import threading

from ...core.exceptions import InternalError, ModelNotReady
from ...core.logging import get_logger

logger = get_logger(__name__)

REPO = "AlexWortega/openjev"
SUBFOLDER = "qwen3.5-4b-nli-v2"
TEMPLATE = "Premise: {premise}\nHypothesis: {hypothesis}"


# Premise: 전제, Hypothesis: 가설
# 전제 : OCR로 읽은 사진 원문 전체
# 가설 : 영수증 판정 결과 이 값들중 이것이다. -> 반복으로 판단  
# 미터기에서는 100원 이상 숫자를 골라 그 후보마다 물어봄. 
# 가장 지지 점수가 높은 1개 결과가 pass일때는 cost를 return / /  reject, review이면 None return 


class SlmEngine:
    def __init__(self, device: str) -> None:
        self.device = device
        self.model = None
        self.tokenizer = None
        self.template = TEMPLATE
        # 4B 모델이 두 번 올라가지 않도록 로드만 잠근다. 추론 순서는 호출하는 쪽에서 정한다.
        self.lock = threading.Lock()

    def predict(self, pairs: list[tuple[str, str]]) -> list[list[float]]:
        self.load()
        import torch

        texts = [
            self.template.replace("{premise}", premise.strip()).replace("{hypothesis}", hypothesis)
            for premise, hypothesis in pairs
        ]
        try:
            encoded = self.tokenizer(
                texts, truncation=True, max_length=4096, padding=True, return_tensors="pt"
            ).to(self.device)
            with torch.inference_mode():
                logits = self.model(**encoded).logits.float()
            return logits.softmax(dim=-1).tolist()
        except Exception as exc:
            logger.exception("slm inference failed")
            raise InternalError() from exc

    def load(self) -> None:
        with self.lock:
            if self.model is not None:
                return
            try:
                import torch
                from transformers import AutoModelForSequenceClassification, AutoTokenizer
            except ImportError as exc:
                logger.exception("torch or transformers is not installed")
                raise ModelNotReady("open model is not ready") from exc
            try:
                tokenizer = AutoTokenizer.from_pretrained(REPO, subfolder=SUBFOLDER)
                tokenizer.pad_token = tokenizer.pad_token or tokenizer.eos_token
                tokenizer.padding_side = "right"
                dtype = torch.float16 if self.device == "cuda" else torch.float32
                model = AutoModelForSequenceClassification.from_pretrained(
                    REPO, subfolder=SUBFOLDER, dtype=dtype
                ).to(self.device).eval()
                text_config = model.config.get_text_config()
                if text_config.pad_token_id is None:
                    text_config.pad_token_id = tokenizer.pad_token_id
            except Exception as exc:
                logger.exception("slm model load failed")
                raise ModelNotReady("open model is not ready") from exc
            self.tokenizer = tokenizer
            self.template = getattr(model.config, "nli_template", None) or TEMPLATE
            self.model = model
