import os

# 이미지 모델(OCR, openjev)을 어디서 돌릴지 정한다. 기능 파이프라인이 llm(..., device=)로 넘긴다.
# - "cpu", "mps": 이 서버에서 돌린다. 로컬 테스트용이다.
# - "cuda": 이 서버의 GPU에서 돌린다.
# - "modal": Modal T4에서 돌린다(v3). 비워 두고 MODAL_TOKEN_ID만 있어도 이 값이 된다.
# - "off": 로컬 모델 없이 항상 luna로 처리한다(v2). 아무것도 없을 때의 기본값이다.
IMAGE_DEVICE = os.getenv("IMAGE_DEVICE") or ("modal" if os.getenv("MODAL_TOKEN_ID") else "off")
LOCAL_DEVICES = ("cpu", "mps", "cuda")
