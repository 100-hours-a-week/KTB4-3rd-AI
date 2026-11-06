# 이미지 모델 장치 결정 순서(Modal _1 → LOCAL_DEVICE → luna)를 본다.
# Modal _1 토큰 사용은 tests/llm/test_modal_pipe.py::test_prepare_uses_existing_app이 본다.
from app.core import config


def test_resolve_device(monkeypatch):
    monkeypatch.setattr(config, "local_device", lambda: "mps")
    modal_1 = {"MODAL_TOKEN_ID_1": "id", "MODAL_TOKEN_SECRET_1": "secret"}
    assert config.resolve_device({}) == "off"
    assert config.resolve_device({"LOCAL_DEVICE": "FALSE"}) == "off"
    assert config.resolve_device({"LOCAL_DEVICE": "true"}) == "mps"
    assert config.resolve_device({**modal_1, "LOCAL_DEVICE": "TRUE"}) == "modal"
    assert config.resolve_device({"MODAL_TOKEN_ID_1": "id"}) == "off"  # Secret이 없으면 Modal을 쓰지 않는다
    assert config.resolve_device({"MODAL_TOKEN_ID_2": "id", "MODAL_TOKEN_SECRET_2": "s"}) == "off"  # _2는 혼잡도용
    assert config.resolve_device({"IMAGE_DEVICE": "mps", "MODAL_TOKEN_ID": "id"}) == "off"  # 예전 이름은 보지 않는다

