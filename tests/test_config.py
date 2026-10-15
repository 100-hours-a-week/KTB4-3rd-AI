# 이미지 모델 장치 결정 순서(Modal _1 → LOCAL_DEVICE → luna)와 Modal _1 토큰 사용을 본다.
import sys
import types

from app.core import config
from app.llm.providers.modal_gpu import ModalEngine


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


def test_modal_uses_slot_1_tokens(monkeypatch):
    seen = {}

    class Client:
        @staticmethod
        def from_credentials(token_id, token_secret):
            seen["credentials"] = (token_id, token_secret)
            return "client"

    class Cls:
        @staticmethod
        def from_name(app, name, client=None):
            seen["lookup"] = client
            return Cls()

        def hydrate(self, client=None):
            seen["hydrate"] = client
            return lambda: "remote"

    monkeypatch.setitem(sys.modules, "modal", types.SimpleNamespace(Client=Client, Cls=Cls))
    monkeypatch.setenv("MODAL_TOKEN_ID_1", "id-1")
    monkeypatch.setenv("MODAL_TOKEN_SECRET_1", "secret-1")
    monkeypatch.setenv("MODAL_TOKEN_ID_2", "id-2")
    assert ModalEngine("1").load() == "remote"
    assert seen == {"credentials": ("id-1", "secret-1"), "lookup": "client", "hydrate": "client"}
