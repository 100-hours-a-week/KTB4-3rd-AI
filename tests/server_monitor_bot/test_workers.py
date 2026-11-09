# W1 조사 루프(도구 라운드 한도·묶음 차단)와 W2 재작성 루프를 가짜 모델로 확인한다.
import time

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from app.Server_Monitor_Bot.ops_agent.common.llm import Budget
from app.Server_Monitor_Bot.ops_agent.workers.investigate import build_investigate, run_investigate
from app.Server_Monitor_Bot.ops_agent.workers.write_verify import build_write_verify, run_write

from .fakes import FakeChat, call


@tool(description="테스트 도구")
def ping(n: int) -> dict:
    return {"evidence_id": f"metric:x@{n}", "summary": f"값 {n}", "data": {"n": n}}


@tool(description="묶음 밖 도구")
def secret() -> dict:
    return {"evidence_id": "bad", "summary": "", "data": {}}


COUNT = {"n": 0}


@tool(description="느린 도구")
def slow(n: int) -> dict:
    COUNT["n"] += 1
    time.sleep(0.5 if n else 0)
    return {"evidence_id": f"metric:s@{n}", "summary": "token=abc 비밀", "data": {}}


TOOLS = {"ping": ping, "secret": secret, "slow": slow}


def run(replies, tool_set=("ping",), max_rounds=2, timeout=10.0):
    llm = FakeChat(replies)
    out = run_investigate(build_investigate(llm, TOOLS, timeout), system="s", goal="g", context="c", tool_set=list(tool_set),
                          max_rounds=max_rounds, budget=Budget())
    return llm, out


def test_round_limit_and_last_call_without_tools():
    replies = [AIMessage(content="", tool_calls=[call("ping", {"n": i}, f"c{i}")]) for i in range(5)]
    llm, out = run(replies, max_rounds=2)
    assert out["rounds"] == 2 and out["stop_reason"] == "max_rounds"
    assert llm.tool_choices == ["auto", "auto", "none"]
    assert [e["id"] for e in out["evidence"]] == ["metric:x@0", "metric:x@1"]


def test_blocked_tool_not_executed():
    replies = [AIMessage(content="", tool_calls=[call("secret", {}, "c1")]), AIMessage(content="끝")]
    llm, out = run(replies, max_rounds=3)
    assert out["evidence"][0]["id"] is None and "허용되지 않은 도구" in out["evidence"][0]["summary"]
    assert out["stop_reason"] == "enough"


GOOD = {"value": 3, "ttl_min": 30, "reason": "동시 최대 6", "evidence_ids": ["metric:x@1"]}


def test_write_verify_rewrites_until_valid():
    llm = FakeChat(structured=[{**GOOD, "value": 99}, GOOD])
    inputs = {"policy": {"min": 1, "max": 8}, "max_ttl_min": 30, "stage": "dev"}
    out = run_write(build_write_verify(llm), schema="adjust_proposal", instructions="i", inputs=inputs,
                    evidence=[{"id": "metric:x@1", "data": "6"}], max_retries=1, budget=Budget())
    assert out["valid"] and out["retries"] == 1 and not out["first_pass"]


def test_write_verify_stops_at_limit():
    llm = FakeChat(structured=[{**GOOD, "value": 99}, {**GOOD, "value": 99}, GOOD])
    out = run_write(build_write_verify(llm), schema="adjust_proposal", instructions="i",
                    inputs={"policy": {"min": 1, "max": 8}, "max_ttl_min": 30}, evidence=[{"id": "metric:x@1", "data": "6"}],
                    max_retries=1, budget=Budget())
    assert not out["valid"] and out["retries"] == 1 and llm.structured == [GOOD]


def test_timeout_cache_and_mask():
    COUNT["n"] = 0
    replies = [AIMessage(content="", tool_calls=[call("slow", {"n": 0}, "a")]), AIMessage(content="", tool_calls=[call("slow", {"n": 0}, "b")]),
               AIMessage(content="", tool_calls=[call("slow", {"n": 1}, "c")])]
    _, out = run(replies, tool_set=("slow",), max_rounds=3, timeout=0.1)
    assert COUNT["n"] == 2
    assert "<masked>" in out["evidence"][0]["summary"] and "abc" not in out["evidence"][1]["summary"]
    assert "시간 초과" in out["evidence"][2]["summary"]
