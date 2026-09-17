# 모델 이름을 받아 openai / slm / ocr 엔진으로 보내는 라우터
from .providers.ocr import OcrEngine
from .providers.openai import OpenAIEngine
from .providers.slm import SlmEngine
from .types import ModelName

_engines: dict[str, object] = {}


def llm(name: ModelName):
    if name in _engines:
        return _engines[name]
    if name == "openai":
        engine = OpenAIEngine()
    elif name == "slm":
        engine = SlmEngine()
    elif name == "ocr":
        engine = OcrEngine()
    else:
        raise ValueError(f"unknown model: {name}")
    _engines[name] = engine
    return engine