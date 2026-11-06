# repos 통합 확인(실제 MySQL + Qdrant). SERVER_BOT_TEST_DB_URL이 있을 때만 돌고, Qdrant는 SERVER_BOT_TEST_QDRANT_URL이 있으면 서버, 없으면 메모리 모드.
import hashlib
import os
import threading
import time

import pytest
from qdrant_client import QdrantClient

from app.Server_Monitor_Bot.config.settings import load
from app.Server_Monitor_Bot.knowledge.schema import load_seed
from app.Server_Monitor_Bot.store.mysql import MySQLDB
from app.Server_Monitor_Bot.store.repos import Repos
from app.Server_Monitor_Bot.store.repos.base import dt
from app.Server_Monitor_Bot.store.repos.metrics import bucket_rows
from app.Server_Monitor_Bot.store.vector import VectorDB

URL = os.environ.get("SERVER_BOT_TEST_DB_URL")
pytestmark = pytest.mark.skipif(not URL, reason="SERVER_BOT_TEST_DB_URL 없음")
TABLES = ("request_results", "llm_calls", "app_logs", "resource_samples", "monitor_health", "metrics_1m", "alerts", "runtime_flags",
          "scheduled_events", "tg_state", "incidents", "chat_messages", "adjustments", "agent_runs", "tg_messages", "drill_runs",
          "eval_results", "knowledge_drafts", "tg_members", "chat_summaries")
QDRANT = os.environ.get("SERVER_BOT_TEST_QDRANT_URL")


class FakeEmbed:
    fail = False

    def one(self, text: str) -> list[float]:
        return self.many([text])[0]

    def many(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("embed down")
        return [[b / 255 + 0.01 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


@pytest.fixture(scope="module")
def repos():
    db = MySQLDB.from_url(URL)
    for t in TABLES:
        db.execute(f"DELETE FROM {t}")
    client = QdrantClient(url=QDRANT, api_key=os.environ.get("SERVER_BOT_TEST_QDRANT_KEY"), https=False) if QDRANT else QdrantClient(":memory:")
    vec = VectorDB(stage="test", url=None, api_key=None, dim=8, client=client)
    for name in ("knowledge", "docs", "chat"):
        vec.drop(name)
        vec.ensure(name)
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
    repos.records.save_request_results([{"request_id": "img1", "ts": now, "task_id": "t", "feature": "receipt", "result": "fail",
                                         "code": "IMAGE_FETCH_FAILED", "step_id": "image_fetch", "reason": "http_5xx", "latency_ms": 40}])
    c = repos.metrics.compare("receipt", "luna_call", dt(now - 60), dt(now + 60))
    assert (c["calls"], c["fails"]) == (5, 4)


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
    assert stats["inserted"] == 24 and k.seed_done(items, h) and len(k.active()) == 24
    assert k.upsert_seed(items, h)["inserted"] == 0
    hits = k.search("OpenAI 429", step_ids=["image_fetch"], limit=5)
    assert len(hits) == 5 and "image_fetch" in hits[0]["step_ids"]
    body = {f: v for f, v in items[9].items() if f != "id"}
    did = k.save_draft(op="update", target_id="KN_OPENAI_RATE_LIMIT", target_version=1, draft_body={**body, "resolution": "동시 호출 축소 + 조직 한도 확인"},
                       reason="대화", source_refs=["chat:1"], incident_id=None, chat_id=99)
    k.embed.fail = True
    assert "임베딩에 실패" in k.apply_draft(did, 1)["message"] and k.get_draft(did)["status"] == "pending"
    k.embed.fail = False
    results = []
    threads = [threading.Thread(target=lambda: results.append(k.apply_draft(did, 1))) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(r["ok"] for r in results) == [False, True]
    cur = k.get("KN_OPENAI_RATE_LIMIT")
    assert cur["version"] == 2 and cur["source"] == "chat" and cur["resolution"].startswith("동시 호출 축소")
    old = k.vec.payloads("knowledge", ["knowledge:KN_OPENAI_RATE_LIMIT@v1"])["knowledge:KN_OPENAI_RATE_LIMIT@v1"]
    assert old["status"] == "archived" and old["version"] == 1 and old["change_reason"] == "대화"
    assert all(h["status"] == "active" for h in k.search("OpenAI 한도", limit=5))
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
    gone = k.save_draft(op="delete", target_id="KN_C000001", target_version=1, draft_body={}, reason="중복", source_refs=[], incident_id=None,
                        chat_id=99)
    assert k.apply_draft(gone, 1)["ok"] and k.get("KN_C000001")["status"] == "deleted"
    assert "KN_C000001" not in [x["id"] for x in k.active()] and k.titles("JSON") == []
    assert k.upsert_seed(items, h)["diverged"] == ["KN_OPENAI_RATE_LIMIT"]


def test_doc_draft_replaces_section(repos):
    from app.Server_Monitor_Bot.docs.models import Source
    from app.Server_Monitor_Bot.docs.pipeline import ingest

    ingest(Source("운영메모.md", "# 운영 메모\n\n## 롤백\n이전 이미지로 되돌린다\n\n## 연락\n클라우드팀".encode(), "telegram", 1), repos.docs)
    old = repos.docs.chunk("운영메모", 0)
    did = repos.knowledge.save_draft(op="update", target_id=old["key"], target_version=None, draft_body={"text": "이전 태스크 정의 리비전으로 되돌린다"},
                                     reason="정정", source_refs=[], incident_id=None, chat_id=99, kind="doc")
    res = repos.knowledge.apply_draft(did, 1)
    new = repos.docs.chunk("운영메모", 0)
    assert res["ok"] and new["text"] == "이전 태스크 정의 리비전으로 되돌린다" and new["key"] != old["key"] and repos.docs.get(old["key"]) is None
    assert not repos.knowledge.apply_draft(did, 1)["ok"]


def test_chat_adjust_drills(repos):
    row = repos.chat.save_turn(chat_id=99, message_id=500, reply_to=None, question="429 왜 났어?", answer="한도 초과", incident_id=None,
                               intent="answer", evidence=["llm:1"])
    repos.chat.fill_bot_message(row, 501)
    assert [m["role"] for m in repos.chat.recent(99)] == ["user", "bot"]
    found = repos.chat.search("429 왜 났어?")
    assert found[0]["question"] == "429 왜 났어?" and found[0]["answer"] == "한도 초과"
    repos.chat.save_turn(chat_id=99, message_id=502, reply_to=None, question="키는 api_key=sk-abcdefghijklmnopqrstuvwxyz123456 야",
                         answer="확인", incident_id=None, intent="answer", evidence=[])
    assert "sk-abcdefghijkl" not in repos.db.fetch_one("SELECT text FROM chat_messages WHERE chat_id = 99 AND message_id = 502")["text"]
    repos.chat.save_summary(99, "요약", 1)
    repos.chat.save_summary(99, "요약2", 2)
    assert repos.chat.summary_of(99) == {"summary": "요약2", "upto_id": 2} and repos.chat.summary_of(1) == {"summary": "", "upto_id": 0}
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


def test_decisions_apply_once_and_messages_of(repos):
    a = repos.adjust
    aid = a.propose(incident_id=None, knowledge_id=None, knob="llm.timeout_s", old_value=0, new_value=20, ttl_min=30)
    assert a.apply(aid, "llm.timeout_s", 20, time.time() + 600, {}) is True
    assert a.apply(aid, "llm.timeout_s", 99, time.time() + 600, {}) is False
    assert [f["value"] for f in a.flags() if f["key"] == "llm.timeout_s"] == [20]
    assert a.set_status(aid, "rejected", expect=("proposed",)) == 0 and a.get(aid)["status"] == "applied"
    assert a.rollback(aid, "llm.timeout_s") is True and a.rollback(aid, "llm.timeout_s") is False
    assert a.get(aid)["status"] == "rolled_back" and not [f for f in a.flags() if f["key"] == "llm.timeout_s"]
    c = repos.coord
    c.link_message(31, 1, "member", "list")
    c.link_message(32, 2, "member", "list")
    c.link_message(33, 1, "adjustment", aid)
    repos.db.execute("UPDATE tg_messages SET ts = NOW(3) - INTERVAL 25 HOUR WHERE message_id = 32")
    assert c.messages_of("member", "list", 24) == [(1, 31)]
    assert sorted(c.messages_of("member", "list")) == [(1, 31), (2, 32)] and c.messages_of("adjustment", aid) == [(1, 33)]
    c.link_message(34, 1, "adjustment", aid)
    c.link_message(34, 1, "incident", "I-1")
    assert c.ref_of(1, 34) == ("incident", "I-1") and (1, 34) in c.messages_of("adjustment", aid)


def test_reindex_rebuilds_empty_collections(repos):
    from types import SimpleNamespace

    from app.Server_Monitor_Bot.pipelines.retention import reindex

    repos.db.execute("UPDATE chat_messages SET ts = NOW(3) - INTERVAL 10 DAY WHERE chat_id = 99")
    old = repos.db.fetch_one("SELECT UNIX_TIMESTAMP(ts) AS t FROM chat_messages WHERE chat_id = 99 AND role = 'user'")["t"]
    for name in ("knowledge", "docs", "chat"):
        repos.knowledge.vec.drop(name)
    ctx = SimpleNamespace(repos=repos, settings=load(), telegram=None, image_tag="t")
    out = reindex(ctx)
    assert out["rebuilt"] == ["chat"] and out["chat"] == 2 and out["sync"]["inserted"] == 24 and out["sync"]["docs"]["docs"] == 9
    assert repos.knowledge.vec.payloads("chat", ["chat:99:500"])["chat:99:500"]["ts"] == int(old)
    again = reindex(ctx)
    assert again["rebuilt"] == [] and again["sync"]["skipped"] and again["sync"]["docs"]["skipped"]


def test_tls_modes_against_server():
    if "ssl=required" not in URL:
        pytest.skip("TLS 강제 서버가 아님")
    assert MySQLDB.from_url(URL).fetch_one("SHOW SESSION STATUS LIKE 'Ssl_cipher'")["Value"]
    ca = os.environ.get("SERVER_BOT_TEST_CA")
    if ca:
        with pytest.raises(Exception):
            MySQLDB.from_url(URL.replace("ssl=required", f"ssl=verify&ssl_ca={ca}")).lock_connection()


def test_members_flow(repos):
    m = repos.members
    repos.db.execute("DELETE FROM tg_members")
    assert m.request(7, 70, "홍길동", "hong") == "new" and m.request(7, 70, "홍길동", "hong") == "pending"
    assert [r["user_id"] for r in m.pending()] == [7] and not m.is_member(7)
    assert m.approve(7, 1) == 1 and m.approve(7, 1) == 0 and m.is_member(7) and m.chats() == [70]
    assert m.request(7, 70, None, None) == "approved"
    m.request(8, 80, None, None)
    assert m.reject(8) == 1 and m.get(8) is None and m.request(8, 80, None, None) == "new"
    assert m.reject(7) == 0 and m.is_member(7)
