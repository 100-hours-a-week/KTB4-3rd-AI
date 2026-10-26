# W2 검증기: 근거 ID·수치·파일:줄·비밀 패턴과 스키마별 규칙을 확인한다.
from app.Server_Monitor_Bot.ops_agent.common.validators import BY_SCHEMA, common

EV = [{"id": "metric:receipt.luna_call@14:02", "summary": "실패 4/9", "data": '{"fails": 4, "total": 9, "ms": 31000}'},
      {"id": "code:app/llm/providers/openai.py:101", "summary": "", "data": '{"cited": ["app/llm/providers/openai.py:101"]}'}]


def test_common_rules():
    good = {"summary": "실패 4/9", "cause_text": "31000ms 지연, openai.py:101 재시도", "evidence_ids": ["metric:receipt.luna_call@14:02"]}
    assert common(good, EV, {}) == []
    bad = {"summary": "실패율 37건", "cause_text": "app/main.py:12 문제 token=abc", "evidence_ids": ["llm:999"]}
    errors = common(bad, EV, {})
    assert any("없는 근거 ID" in e for e in errors) and any("37" in e for e in errors)
    assert any("파일:줄" in e for e in errors) and any("비밀" in e for e in errors)
    assert common({"summary": "14:02 이후 2026-10-26 44% 증가", "evidence_ids": []}, EV, {}) == []


def test_schema_rules():
    inputs = {"policy_keys": ["llm.max_concurrency"], "active_knowledge_ids": ["KN_A"]}
    r = {"step_id": "luna_call", "policy_key": "nope", "knowledge_ids": ["KN_B"], "unknown": True, "confidence": "high"}
    assert len(BY_SCHEMA["diagnosis_report"](r, inputs)) == 3
    assert BY_SCHEMA["chat_answer"]({"intent": "knowledge_update", "target_knowledge_id": "KN_X", "edit_request": "x"}, inputs)
    item = {"title": "t", "features": [], "step_ids": ["luna_call"], "match": {"step_id": ["luna_call"], "host": None}, "symptom": "s",
            "cause": "c", "resolution": "r", "checks": [], "policy_key": None, "alert": "diagnose", "severity": "low"}
    assert BY_SCHEMA["knowledge_draft"]({"op": "add", "target_id": None, "knowledge": item}, {**inputs, "op": "add"}) == []
    assert BY_SCHEMA["knowledge_draft"]({"op": "add", "knowledge": {**item, "match": {"reason": ["x"]}, "resolution": ""}},
                                        {**inputs, "op": "update"})


def test_exact_ids_and_number_tokens():
    errors = common({"evidence_ids": ["llm:1"], "summary": "450ms"}, [{"id": "llm:12,llm:13", "summary": "4500ms"}], {})
    assert len(errors) == 2
    assert common({"evidence_ids": ["llm:13"], "summary": "4500ms"}, [{"id": "llm:12,llm:13", "summary": "4500ms"}], {}) == []
