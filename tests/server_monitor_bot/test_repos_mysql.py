# repos 통합 확인(실제 MySQL + Qdrant 메모리 모드). SERVER_BOT_TEST_DB_URL이 있을 때만 돈다.
import hashlib
import os
import time

import pytest
from qdrant_client import QdrantClient

from app.Server_Monitor_Bot.config.settings import load
from app.Server_Monitor_Bot.knowledge.schema import load_seed
from app.Server_Monitor_Bot.store.mysql import MySQLDB
from app.Server_Monitor_Bot.store.repos import Repos
from app.Server_Monitor_Bot.store.repos.metrics import bucket_rows
from app.Server_Monitor_Bot.store.vector import VectorDB

URL = os.environ.get("SERVER_BOT_TEST_DB_URL")
pytestmark = pytest.mark.skipif(not URL, reason="SERVER_BOT_TEST_DB_URL 없음")
TABLES = ("request_results", "llm_calls", "app_logs", "resource_samples", "monitor_health", "metrics_1m", "alerts", "runtime_flags",
          "scheduled_events", "tg_state", "incidents", "chat_messages", "adjustments", "agent_runs", "tg_messages", "drill_runs",
          "eval_results", "knowledge", "knowledge_versions", "knowledge_drafts")


class FakeEmbed:
    def one(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode()).digest()
        return [b / 255 for b in h[:8]]


@pytest.fixture(scope="module")
def repos():
    db = MySQLDB.from_url(URL)
    for t in TABLES:
        db.execute(f"DELETE FROM {t}")
    vec = VectorDB(stage="test", url=None, api_key=None, dim=8, client=QdrantClient(":memory:"))
    vec.ensure("knowledge")
    vec.ensure("chat")
    return Repos(db, vec, FakeEmbed(), load().policy)


def test_records_window_and_metrics(repos):
    now = time.time()
    results = [{"request_id": f"r{i}", "ts": now, "task_id": "t", "feature": "receipt", "result": "fail", "code": "UPSTREAM_FAILED",
                "step_id": "luna_call", "reason": "http_4xx", "status": 429, "error_code": "rate_limit_exceeded", "latency_ms": 900}
               for i in range(4)] + [{"request_id": "ok1", "ts": now, "task_id": "t", "feature": "receipt", "result": "ok", "code": "OK",
                                      "step_id": "respond", "latency_ms": 500}]
    calls = [{"ts": now, "request_id": "r0", "feature": "receipt", "name": "api_1", "device": "off", "model": "m", "prompt_ver": "abcd1234",
              "ms": 800, "result": "fail", "status": 429, "error_code": "rate_limit_exceeded", "input_tokens": 10, "output_tokens": 2}]
    repos.records.save_request_results(results)
    repos.records.save_request_results(results[:1])
    repos.records.save_llm_calls(calls)
    repos.records.save_app_logs([{"ts": now, "task_id": "t", "level": "ERROR", "logger": "app.x", "head": "openai request failed",
                                  "message": "openai request failed: reason=http_4xx status=429", "request_id": "r0"}])
    repos.metrics.upsert(bucket_rows(results, calls))
    repos.metrics.upsert(bucket_rows(results, calls))
    w = repos.metrics.window(300)
    g = [x for x in w["groups"] if x["step_id"] == "luna_call"][0]
    assert (int(g["n"]), int(g["fails"]), g["status"], g["error_code"], w["totals"]["receipt"]) == (4, 4, 429, "rate_limit_exceeded", 5)
    assert repos.metrics.step_failures("receipt", "luna_call", 5)["now"]["fails"] == 4
    assert repos.metrics.llm_summary("receipt", 5)["calls"] == 1
    assert repos.records.request_trace("r0")["llm_calls"][0]["status"] == 429
    assert repos.records.recent_logs(5, "openai")[0]["head"] == "openai request failed"
    m = repos.db.fetch_one("SELECT calls, fails FROM metrics_1m WHERE step_id = 'luna_call'")
    assert (m["calls"], m["fails"]) == (8, 8)


def test_alert_claim_and_events(repos):
    c = repos.coord
    assert c.claim_alert("receipt.luna_call.http_4xx", 3, 4, "t1")
    assert not c.claim_alert("receipt.luna_call.http_4xx", 3, 4, "t2")
    assert c.held("receipt.luna_call.http_4xx") == 4
    assert c.claim_alert("receipt.luna_call.http_4xx", 3, 12, "t2")
    eid = c.schedule({"type": "adapt.observe"}, time.time() - 1)
    claimed = c.claim_events("t1")
    assert [e for i, e in claimed if i == eid][0]["type"] == "adapt.observe" and c.claim_events("t2") == []
    c.event_done(eid)
    c.set_tg_offset("test", 41)
    c.set_tg_offset("test", 42)
    assert c.tg_offset("test") == 42
    c.link_message(7, 99, "alert", "receipt.luna_call.http_4xx@1")
    assert c.ref_of(99, 7) == ("alert", "receipt.luna_call.http_4xx@1")
    c.record_run({"run_id": "run1", "event_id": "e1", "event_type": "alert.pipeline", "tokens": 100})
    assert c.seen_event("e1") and not c.seen_event("e2") and c.tokens_today(False) == 100


def test_seed_draft_save_and_versions(repos):
    k = repos.knowledge
    items, h = load_seed()
    stats = k.upsert_seed(items, h)
    assert stats["inserted"] == 23 and k.seed_done(items, h)
    assert k.upsert_seed(items, h)["inserted"] == 0
    assert k.search("OpenAI 429", limit=3)
    did = k.save_draft(op="update", target_id="KN_OPENAI_RATE_LIMIT", target_version=1,
                       draft_body={**{f: v for f, v in items[9].items() if f != "id"}, "resolution": "동시 호출 축소 + 조직 한도 확인"},
                       reason="대화", source_refs=["chat:1"], incident_id=None, chat_id=99)
    res = k.apply_draft(did, 1)
    assert res["ok"] and k.get("KN_OPENAI_RATE_LIMIT")["version"] == 2 and k.get("KN_OPENAI_RATE_LIMIT")["source"] == "chat"
    assert not k.apply_draft(did, 1)["ok"]
    stale = k.save_draft(op="delete", target_id="KN_OPENAI_RATE_LIMIT", target_version=1, draft_body={}, reason="r",
                         source_refs=[], incident_id=None, chat_id=99)
    assert "바뀌었습니다" in k.apply_draft(stale, 1)["message"]
    iid = repos.incidents.create(trigger_key="receipt.luna_parse.not_json", feature="receipt", step_id="luna_parse", summary="s",
                                 cause_text="c", resolution_text="r", confidence="medium", evidence=["llm:1"], knowledge_refs=[],
                                 policy_key=None, drill=False, agent_run_id=None)
    add = k.save_draft(op="add", target_id=None, target_version=None,
                       draft_body={"title": "모델 버전 변경 후 JSON 깨짐", "features": ["receipt"], "step_ids": ["luna_parse"],
                                   "match": {"step_id": ["luna_parse"], "reason": ["not_json"]}, "symptom": "s", "cause": "c",
                                   "resolution": "r", "checks": [], "policy_key": None, "alert": "diagnose", "severity": "medium"},
                       reason="r", source_refs=[], incident_id=iid, chat_id=99)
    res = k.apply_draft(add, 1)
    assert res["ok"] and res["knowledge"]["id"] == "KN_C000001" and repos.incidents.get(iid)["status"] == "resolved"
    assert k.upsert_seed(items, h)["diverged"] == ["KN_OPENAI_RATE_LIMIT"]


def test_chat_adjust_drills(repos):
    row = repos.chat.save_turn(chat_id=99, message_id=500, reply_to=None, question="429 왜 났어?", answer="한도 초과", incident_id=None,
                               intent="answer", evidence=["llm:1"])
    repos.chat.fill_bot_message(row, 501)
    assert [m["role"] for m in repos.chat.recent(99)] == ["user", "bot"] and repos.chat.search("429")
    a = repos.adjust
    aid = a.propose(incident_id=None, knowledge_id="KN_OPENAI_RATE_LIMIT", knob="llm.max_concurrency", old_value=0, new_value=3, ttl_min=30)
    assert a.active_for_knob("llm.max_concurrency")["id"] == aid
    a.apply(aid, "llm.max_concurrency", 3, time.time() - 1, {"fail_rate": 0.4})
    assert a.flags()[0]["value"] == 3
    old = a.propose(incident_id=None, knowledge_id=None, knob="image.max_side_px", old_value=0, new_value=1600, ttl_min=30)
    repos.db.execute("UPDATE adjustments SET created_at = NOW(3) - INTERVAL 25 HOUR WHERE id = %s", (old,))
    assert a.active_for_knob("image.max_side_px") is None
    assert [r["key"] for r in a.expire_due()] == ["llm.max_concurrency"] and a.get(aid)["status"] == "expired" and a.flags() == []
    rid = repos.drills.start("S-a1b2", "A", 7)
    repos.drills.stop(rid)
    repos.drills.guess(rid, 1, "한도", 1000, True, "같은 원인")
    repos.drills.guess(rid, 2, "한도", 500, True, "같은 원인")
    assert repos.drills.get(rid)["status"] == "closed"
    repos.drills.add_eval({"drill_run_id": rid, "scenario_id": "S-a1b2", "group": "A", "config": "agent", "run_no": 1, "cause_hit": True})
