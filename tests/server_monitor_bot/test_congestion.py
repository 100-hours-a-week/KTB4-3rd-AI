# 혼잡도(/congestion-analyses): feat/congestion-analysis 브랜치 로그 문구 그대로 → 기록·단계·사유·대체 처리·LLM 호출, 알림 문구, 지식 신호, Kafka consumer 경로(request done 없음) 검사.
import time

from app.Server_Monitor_Bot.channels.telegram import render as r
from app.Server_Monitor_Bot.hooks.assemble import Assembler
from app.Server_Monitor_Bot.knowledge.schema import validate

SVC, CLIENT, SPOT = "app.features.congestion.service", "app.features.congestion.model_client", "app.features.congestion.spot_extractor"


def log(message, level="INFO", rid="c1", logger=SVC):
    return {"ts": time.time(), "level": level, "logger": logger, "message": message, "request_id": rid, "exc": None}


def run(lines):
    a = Assembler(outbox=None)
    out = []
    for item in lines:
        out.extend(a.handle("log", item))
    return {k: [x for kind, x in out if kind == k] for k in ("request_result", "llm_call", "app_log")}


def done(code, status, rid="c1"):
    return log(f"request done: path=/congestion-analyses status={status} code={code} ms=4200", rid=rid, logger="app.main")


def test_judge_invalid_is_recorded():
    out = run([log("congestion analysis started: spot_id=s1 event_id=e1 bundles=1 posts=3 comments=2"),
               log("congestion model call done: api=responses model=gpt-6-luna attempts=1 ms=3100 tokens_in=1200 tokens_out=300", logger=CLIENT),
               log("congestion judge invalid: reason=id_mismatch model=gpt-6-luna", "WARNING", logger=SPOT),
               log("congestion analysis failed: spot_id=s1 event_id=e1 code=CONGESTION_PARSE_FAILED ms=4100", "WARNING"),
               done("CONGESTION_PARSE_FAILED", 502)])
    res = out["request_result"][0]
    assert (res["feature"], res["result"], res["code"], res["step_id"], res["reason"]) == \
        ("congestion", "fail", "CONGESTION_PARSE_FAILED", "congestion_judge", "id_mismatch")
    call = out["llm_call"][0]
    assert (call["feature"], call["name"], call["model"], call["ms"], call["input_tokens"], call["output_tokens"], call["result"]) == \
        ("congestion", "congestion_responses", "gpt-6-luna", 3100, 1200, 300, "ok")


def test_model_failure_key_missing_and_fallback():
    failed = run([log("congestion model call failed: api=responses reason=http_4xx status=429 error=requests/rate_limit_exceeded ms=900 "
                      "hint=OPENAI_API_KEY_3", "ERROR", logger=CLIENT),
                  done("UPSTREAM_FAILED", 502)])
    res, call = failed["request_result"][0], failed["llm_call"][0]
    assert (res["step_id"], res["reason"], res["status"], res["error_code"]) == ("luna_call", "http_4xx", 429, "rate_limit_exceeded")
    assert (call["result"], call["status"], call["error_code"], call["reason"]) == ("fail", 429, "rate_limit_exceeded", "http_4xx")
    timeout = run([log("congestion model call failed: api=responses reason=timeout status=- error=- ms=30000 hint=OPENAI_API_KEY_3", "ERROR",
                       logger=CLIENT), done("UPSTREAM_FAILED", 502)])
    assert (timeout["llm_call"][0]["error_code"], timeout["llm_call"][0]["status"], timeout["request_result"][0]["reason"]) == (None, None, "timeout")
    key = run([log("congestion model key is missing: key=OPENAI_API_KEY_3 hint=OPENAI_API_KEY_3", "ERROR", logger=CLIENT),
               done("MODEL_NOT_READY", 503)])["request_result"][0]
    assert (key["step_id"], key["reason"]) == ("model_ready", "key_missing")
    ok = run([log("congestion model fallback: api=responses from=gpt-6-luna to=claude-haiku reason=http_5xx", "WARNING", logger=CLIENT),
              done("OK", 200)])["request_result"][0]
    assert (ok["result"], ok["fallback"], ok["fallback_step"]) == ("ok", True, "congestion_model")
    kw = run([log("congestion relevance fallback: reason=timeout method=keyword candidates=3", "WARNING"), done("OK", 200)])["request_result"][0]
    assert kw["fallback_step"] == "congestion_relevance"


def test_alert_lines_and_knowledge_signal():
    sig = {"feature": "congestion", "step_id": "congestion_judge", "reason": "id_mismatch", "status": None, "error_code": None}
    assert r.where_lines(sig) == ["코드: congestion_judge · POST /congestion-analyses → CONGESTION_PARSE_FAILED · "
                                  "app/features/congestion/spot_extractor.py · assess_spot()"]
    assert r.where_lines({**sig, "step_id": "luna_call"})[0].endswith("UPSTREAM_FAILED · app/features/congestion/model_client.py · _call()")
    assert "UPSTREAM_FAILED · app/llm/providers/openai.py" in r.where_lines({**sig, "feature": "receipt", "step_id": "luna_call"})[0]
    assert r.step("congestion_judge") == "판정 출력 검증(congestion_judge)" and r.cause("id_mismatch") == "판정 id 불일치(id_mismatch)"
    item = {"id": "KN_CONGESTION_JUDGE", "title": "t", "symptom": "s", "cause": "c", "resolution": "r", "checks": [], "features": ["congestion"],
            "match": {"step_id": ["congestion_judge"], "reason": ["id_mismatch"]}, "policy_key": None, "alert": "diagnose", "severity": "medium"}
    assert validate(item, ()) == []


def test_consumer_path_without_request_done():
    a = Assembler(outbox=None)
    for item in (log("congestion analysis started: spot_id=s1 event_id=k1 bundles=1 posts=3 comments=2", rid="k1"),
                 log("congestion model call done: api=responses model=gpt-6-luna attempts=1 ms=3100 tokens_in=1200 tokens_out=300",
                     rid="k1", logger=CLIENT),
                 log("congestion analysis failed: spot_id=s1 event_id=k1 code=UPSTREAM_FAILED ms=4100", "WARNING", rid="k1"),
                 log("congestion analysis done: spot_id=s2 level=LOW ms=2100", rid="k2")):
        a.handle("log", item)
    assert a.settle(time.time()) == []
    rows = a.settle(time.time() + 10)
    results = {x["request_id"]: x for kind, x in rows if kind == "request_result"}
    assert (results["k1"]["feature"], results["k1"]["code"], results["k1"]["step_id"], results["k1"]["latency_ms"]) == \
        ("congestion", "UPSTREAM_FAILED", "luna_call", 4100)
    assert (results["k2"]["result"], results["k2"]["latency_ms"]) == ("ok", 2100)
    assert [x["request_id"] for kind, x in rows if kind == "llm_call"] == ["k1"] and not a.bags


def test_http_path_counts_once():
    a = Assembler(outbox=None)
    out = a.handle("log", log("congestion analysis done: spot_id=s1 level=LOW ms=2100")) + a.handle("log", done("OK", 200))
    assert [kind for kind, _ in out].count("request_result") == 1 and a.settle(time.time() + 10) == []


def test_consumer_grace_counts_from_processing_time():
    a = Assembler(outbox=None)
    late = {**log("congestion analysis done: spot_id=s1 level=LOW ms=2100"), "ts": time.time() - 60}
    a.handle("log", late)
    assert a.settle(time.time()) == [] and a.handle("log", done("OK", 200))[0][0] == "request_result"
