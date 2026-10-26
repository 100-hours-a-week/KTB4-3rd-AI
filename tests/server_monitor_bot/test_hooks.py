# 요청 경로 훅: 로그 줄 → request_result 조립, LLM 호출 기록, 마스킹, 플래그·훈련 지점이 꺼져 있을 때 기본값.
import logging
import time

from fastapi.testclient import TestClient

from app.Server_Monitor_Bot.hooks import drill_point, flag, state
from app.Server_Monitor_Bot.hooks.assemble import Assembler
from app.Server_Monitor_Bot.hooks.install import install, uninstall
from app.Server_Monitor_Bot.hooks.mask import mask
from app.Server_Monitor_Bot.store.buffer import FlagCache, Outbox


def log(message, level="INFO", rid="r1", logger="app.features.receipt.image"):
    return {"ts": time.time(), "level": level, "logger": logger, "message": message, "request_id": rid, "exc": None}


def run(items):
    a = Assembler(outbox=None)
    out = []
    for kind, item in items:
        out.extend(a.handle(kind, item))
    return out


def test_fetch_failure_becomes_request_result():
    out = run([("log", log("image download failed: host=s3.example.com reason=http_4xx status=403 ms=120", "WARNING")),
               ("log", log("request done: path=/receipt status=422 code=IMAGE_FETCH_FAILED ms=130", logger="app.main"))])
    kinds = [k for k, _ in out]
    assert kinds == ["app_log", "request_result"]
    r = out[-1][1]
    assert (r["feature"], r["step_id"], r["reason"], r["status"], r["result"], r["image_host"]) == \
           ("receipt", "image_fetch", "http_4xx", 403, "fail", "s3.example.com")


def test_luna_429_and_llm_call_get_feature():
    out = run([("log", log("request route: route=luna reason=device_off")),
               ("llm_call", {"ts": time.time(), "request_id": "r1", "name": "api_1", "status": 429, "error_code": "rate_limit_exceeded"}),
               ("log", log("openai request failed: reason=http_4xx status=429 error=requests/rate_limit_exceeded openai_req=x model=m ms=9 hint=h", "ERROR")),
               ("log", log("request done: path=/taxi_meter status=502 code=UPSTREAM_FAILED ms=900", logger="app.main"))])
    result = [i for k, i in out if k == "request_result"][0]
    call = [i for k, i in out if k == "llm_call"][0]
    assert (result["step_id"], result["status"], result["error_code"], result["route_reason"]) == ("luna_call", 429, "rate_limit_exceeded", "device_off")
    assert call["feature"] == "taxi_meter"


def test_health_is_not_recorded_and_retry_counts_attempts():
    assert run([("log", log("request done: path=/health status=200 code=OK ms=1", logger="app.main"))]) == []
    a = Assembler(outbox=None)
    done = log("request done: path=/receipt status=200 code=OK ms=5", logger="app.main")
    first = a.handle("log", done)[-1][1]
    second = a.handle("log", {**done, "ts": done["ts"] + 1})[-1][1]
    assert (first["attempts"], second["attempts"], second["step_id"]) == (1, 2, "respond")


def test_mask():
    text = "url=https://b.s3.amazonaws.com/x.jpg?X-Amz-Signature=abc token=t1 key=sk-abcdefghijk Authorization=Bearer zz"
    masked = mask(text)
    assert "abc" not in masked and "t1" not in masked and "sk-abc" not in masked and "?<masked>" in masked


def test_hooks_off_by_default():
    assert not state.INSTALLED and flag("llm.max_concurrency", 0) == 0 and drill_point("luna_call") is None


def test_install_taps_real_request(tmp_path, monkeypatch):
    from app.main import app

    assert install("local", tmp_path)
    try:
        FlagCache(tmp_path / "flags.db").replace({"llm.max_concurrency": (3, None)})
        with TestClient(app) as client:
            client.post("/taxi_meter", json={}, headers={"X-Request-ID": "tap-1"})
            assert flag("llm.max_concurrency", 0) == 3
        deadline = time.time() + 5
        rows = []
        while time.time() < deadline and not any(k == "request_result" for _, k, _ in rows):
            time.sleep(0.2)
            rows = Outbox(tmp_path / "buffer.db").read()
        results = [p for _, k, p in rows if k == "request_result"]
        assert results and results[0]["request_id"] == "tap-1" and results[0]["step_id"] == "request_validate"
    finally:
        uninstall()
        logging.getLogger("app").handlers[:] = [h for h in logging.getLogger("app").handlers if type(h).__name__ != "LogTap"]
