# 배포 준비 보완: TLS URL, stg, 재시도 백오프, Qdrant 컬렉션 재생성, 훈련 리포트 연결, 훈련 비교 실행 예산 시각, 경고 로그 stdout.
import io
import logging
import time
from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient

import app.Server_Monitor_Bot as monitor
from app.Server_Monitor_Bot.config.settings import load, stage_of
from app.Server_Monitor_Bot.ops_agent.actions import Actions
from app.Server_Monitor_Bot.ops_agent.common.llm import Budget
from app.Server_Monitor_Bot.ops_agent.diagnose.nodes import Diagnose
from app.Server_Monitor_Bot.runtime import runner
from app.Server_Monitor_Bot.runtime.leader import backoff
from app.Server_Monitor_Bot.store.mysql import RDS_CA, parse_url
from app.Server_Monitor_Bot.store.repos.base import dt
from app.Server_Monitor_Bot.store.vector import VectorDB


def test_parse_url_tls_modes():
    assert parse_url("mysql://a:p%40w@h:3307/ai_dev")["ssl"] is None
    verify = parse_url("mysql://a:p@h/ai_dev?ssl=verify")["ssl"]
    assert verify == {"ca": RDS_CA, "check_hostname": True}
    assert parse_url("mysql://a:p@h/ai_dev?ssl=verify&ssl_ca=/x/ca.pem")["ssl"]["ca"] == "/x/ca.pem"
    assert parse_url("mysql://a:p@h/ai_dev?ssl=required")["ssl"] == {"check_hostname": False, "verify_mode": False}
    assert parse_url("mysql://a:p%40w@h:3307/ai_dev?ssl=required")["password"] == "p@w"
    with pytest.raises(ValueError):
        parse_url("mysql://a:p@h/ai_dev?ssl=maybe")


def test_stg_stage_runs_without_drill():
    s = load({"AI_DB_URL": "mysql://u:p@h/ai_stg?ssl=verify"})
    assert s.enabled and not s.drill_allowed and not s.feature("F8") and s.feature("F1")
    assert VectorDB(stage="stg", url="http://q:6333", api_key="k").name("knowledge") == "stg_knowledge"


def test_backoff_grows_caps_and_resets():
    assert backoff(3, 0) == 3
    for fails, base in ((1, 6), (2, 12), (5, 60), (9, 60)):
        for _ in range(50):
            assert base <= backoff(3, fails) <= base * 1.5


def test_every_backs_off_then_recovers(monkeypatch):
    waits, results = [], iter([RuntimeError, RuntimeError, None, None])

    def fn():
        r = next(results)
        if r:
            raise r("down")

    class Stop:
        def is_set(self):
            return len(waits) >= 4

        def wait(self, t):
            waits.append(t)

    monkeypatch.setattr("app.Server_Monitor_Bot.runtime.leader.random.uniform", lambda a, b: 0)
    monkeypatch.setattr(runner.threading, "Thread", lambda target, name, daemon: SimpleNamespace(start=target))
    runner.every(Stop(), 3, fn, "t")
    assert waits == [6, 12, 3, 3]


def test_vector_recreates_missing_collection():
    v = VectorDB(stage="t", url=None, api_key=None, dim=4, client=QdrantClient(":memory:"))
    v.upsert("chat", "chat:1:2", [0.1, 0.2, 0.3, 0.4], {"ts": 5, "incident_id": None})
    v.c.delete_collection("t_chat")
    assert v.search("chat", [0.1, 0.2, 0.3, 0.4]) == [] and v.count("chat") == 0
    v.upsert("chat", "chat:1:2", [0.1, 0.2, 0.3, 0.4], {"ts": 5, "incident_id": None})
    assert v.count("chat") == 1


def test_drill_report_is_linked_to_incident():
    adds, created = [], []
    repos = SimpleNamespace(incidents=SimpleNamespace(create=lambda **k: created.append(k) or "I-20261027-ab12"),
                            drills=SimpleNamespace(add_eval=adds.append))
    env = SimpleNamespace(ctx=SimpleNamespace(repos=repos), owners=lambda: (1, 2), judge=lambda *a: (True, "같은 원인"))
    report = {"summary": "s", "cause_text": "c", "resolution_text": "r", "step_id": "luna_call", "confidence": "high",
              "evidence_ids": [], "knowledge_ids": [], "policy_key": None, "unknown": False,
              "expected_issues": []}
    drill = {"config": "agent", "run_no": 1, "run_id": "R-1", "scenario_id": "S-1", "group": "A",
             "expected": {"step_id": "luna_call", "cause_text": "c"}}
    out = Diagnose(env).persist({"payload": {"alert_key": "drill.receipt.luna_call.http_4xx", "feature": "receipt"}, "report": report,
                                 "drill": drill, "evidence": [], "budget": Budget(), "run_id": "run-1"})
    assert out["outbox"][0]["ref"] == ("incident", "I-20261027-ab12") and out["outbox"][0]["text"].startswith("🧪 [훈련]") and out["outbox"][0]["html"]
    assert [m["chat_id"] for m in out["outbox"]] == [1, 2]
    assert created[0]["drill"] is True and adds[0]["step_hit"] is True



def test_drill_compare_runs_start_their_budget_when_scheduled():
    run = {"id": "R-1", "scenario_id": "S-7a1c", "group": "A", "started_at": dt(time.time() - 300)}
    repos = SimpleNamespace(drills=SimpleNamespace(current=lambda: run, stop=lambda _id: None),
                            adjust=SimpleNamespace(delete_flag=lambda _k: None),
                            metrics=SimpleNamespace(samples=lambda *a, **k: []))
    env = SimpleNamespace(ctx=SimpleNamespace(repos=repos, telegram=None))
    out = Actions(env).drill_stop({"chat_id": 9}, {})
    assert len(out["followups"]) == 9
    assert all(f["event"]["payload"]["detected_at"] == f["not_before"] for f in out["followups"])


def test_warning_logs_reach_stdout(tmp_path, monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(runner.sys, "stdout", out)
    saved = list(runner.log.handlers)
    runner.log.handlers = []
    try:
        runner.setup_logging(tmp_path)
        runner.log.info("only file")
        runner.log.warning("db connect failed: error=X")
        for h in runner.log.handlers:
            h.flush()
    finally:
        added = [h for h in runner.log.handlers if h not in saved]
        for h in added:
            h.close()
        runner.log.handlers = saved
    file_text = (tmp_path / "monitor.log").read_text()
    assert "only file" in file_text and "db connect failed" in file_text
    assert "db connect failed" in out.getvalue() and "only file" not in out.getvalue()
    assert logging.getLogger("app.Server_Monitor_Bot").propagate is False


def test_stage_comes_from_db_name(caplog, monkeypatch):
    for name in ("local", "dev", "stg", "prod"):
        assert stage_of(f"mysql://u:p@h:3306/ai_{name}?ssl=verify") == name
    assert [stage_of(u) for u in ("mysql://u:p@h/ai_x", "mysql://u:p@h/ai", "mysql://u:p@h/", None)] == ["", "", "", ""]
    monkeypatch.setenv("AI_DB_URL", "mysql://u:secretpw@h/monitor")
    with caplog.at_level(logging.WARNING, logger="app.Server_Monitor_Bot"):
        assert monitor.start() is False
    monkeypatch.setenv("AI_DB_URL", "mysql://ai_prod:pa/ss@h:3306/ai_prod?ssl=verify")
    with caplog.at_level(logging.WARNING, logger="app.Server_Monitor_Bot"):
        assert monitor.start() is False
    warned = [r.getMessage() for r in caplog.records if "db_name" in r.getMessage()]
    assert warned == ["monitor disabled: reason=db_name db=monitor", "monitor disabled: reason=db_name db=?"]
