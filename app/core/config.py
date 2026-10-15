import os
from collections.abc import Mapping

# 이미지 모델(OCR, openjev)을 어디서 돌릴지 정한다. 기능 파이프라인이 llm(..., device=)로 넘긴다.
# 정하는 순서
# 1. MODAL_TOKEN_ID_1, MODAL_TOKEN_SECRET_1이 둘 다 있으면 "modal": Modal T4(cuda)에서 돌린다(v3).
# 2. 아니고 LOCAL_DEVICE=TRUE면 이 서버에서 돌린다. cuda, mps, cpu 순으로 쓸 수 있는 것을 고른다.
# 3. 둘 다 아니면 "off": 이미지 모델 없이 항상 luna(OpenAI)로 처리한다(v2). 기본값이다.
# _1은 이미지(택시미터, 영수증)용이다. _2는 혼잡도용이라 여기서 보지 않는다.
LOCAL_DEVICES = ("cpu", "mps", "cuda")


def local_device() -> str:
    # torch는 LOCAL_DEVICE=TRUE일 때만 불러온다. 없으면 cpu로 두고, 모델 로드 실패 시 luna로 넘어간다.
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def resolve_device(env: Mapping[str, str] = os.environ) -> str:
    if env.get("MODAL_TOKEN_ID_1") and env.get("MODAL_TOKEN_SECRET_1"):
        return "modal"
    if env.get("LOCAL_DEVICE", "").strip().upper() == "TRUE":
        return local_device()
    return "off"


DEVICE = resolve_device()
