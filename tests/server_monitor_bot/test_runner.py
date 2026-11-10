# 분석 프로세스: 일일 작업은 하루 한 번(MySQL 선점), AI_DB_URL 없이 기동, 스키마 재확인, DB 실패 사유 로그, 죽으면 다시 띄우기, ECS 밖 이미지 태그(local·k8s 롤아웃 해시).
import logging
import subprocess
import sys
import threading
from datetime import datetime
from types import SimpleNamespace

import pymysql

import app.Server_Monitor_Bot as monitor
from app.Server_Monitor_Bot import install
from app.Server_Monitor_Bot.runtime import runner
from app.Server_Monitor_Bot.store.mysql import failure_reason


def test_daily_once_per_day(monkeypatch):
    keys, calls = set(), []

    def claim(key, rank, count, task_id, quiet_s=600):
        if key in keys:
            return False
        keys.add(key)
        return True

    ctx = SimpleNamespace(task_id="t", repos=SimpleNamespace(coord=SimpleNamespace(claim_alert=claim)))
    for name in ("report", "retention", "unmatched"):
        monkeypatch.setattr(getattr(runner, name), "run", lambda c, n=name: calls.append(n))
    monkeypatch.setattr(runner, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 10, 27, 9, 5, tzinfo=tz)))
    runner.daily(ctx, {})
    runner.daily(ctx, {})
    assert calls.count("report") == 1 and keys == {"daily:2026-10-27"}


def test_start_without_db_is_quiet(tmp_path):
    env = {"SERVER_BOT_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    res = subprocess.run([sys.executable, "-c", "from app.Server_Monitor_Bot.runtime.runner import main; main()"],
                         env=env, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0 and "Traceback" not in res.stderr
    assert "AI_DB_URL missing" in (tmp_path / "monitor.log").read_text()


def test_analysis_begins_once_when_schema_appears():
    versions, began = iter([0, 0, runner.SCHEMA_VERSION]), []
    repos = SimpleNamespace(coord=SimpleNamespace(schema_version=lambda: next(versions)))
    analysis = threading.Event()

    def begin():
        began.append(1)
        analysis.set()

    for _ in range(5):
        runner.begin_when_ready(repos, analysis, begin)
    assert began == [1]


def test_schema_read_error_counts_as_not_ready():
    def broken():
        raise pymysql.OperationalError(2003, "down")

    assert runner.schema_version(SimpleNamespace(coord=SimpleNamespace(schema_version=broken))) == 0


def test_connect_logs_reason_without_password(monkeypatch, caplog):
    stop = threading.Event()

    def refuse(url, max_connections=2):
        stop.set()
        raise pymysql.OperationalError(2003, "Can't connect ([SSL: CERTIFICATE_VERIFY_FAILED])")

    monkeypatch.setattr("app.Server_Monitor_Bot.store.mysql.MySQLDB.from_url", refuse)
    runner.log.propagate = True
    try:
        with caplog.at_level(logging.WARNING, logger="app.Server_Monitor_Bot"):
            assert runner.connect(SimpleNamespace(db_url="mysql://u:secretpw@h/ai_dev"), stop, None) is None
    finally:
        runner.log.propagate = False
    text = caplog.text
    assert "2003" in text and "CERTIFICATE_VERIFY_FAILED" in text and "secretpw" not in text


def test_failure_reason_hides_non_mysql_messages():
    assert failure_reason(ValueError("Port could not be cast: secretpw")) == "ValueError"
    assert failure_reason(pymysql.OperationalError(1045, "Access denied")) == "1045 Access denied"


def test_watch_respawns_dead_process_and_stops_on_shutdown(monkeypatch):
    class Dead:
        exitcode = -9

        def is_alive(self):
            return False

    stop, spawned = threading.Event(), []

    def spawn(restarts=0):
        spawned.append(restarts)
        if restarts == 2:
            stop.set()
        return Dead()

    monkeypatch.setattr(monitor, "_process", Dead())
    monkeypatch.setattr(monitor, "spawn", spawn)
    monkeypatch.setattr(monitor, "CHECK_S", 0.001)
    monkeypatch.setattr(monitor, "RESPAWN_S", 0.001)
    monkeypatch.setattr(monitor, "RESPAWN_MAX_S", 0.002)
    monitor.watch(stop)
    assert spawned == [1, 2]


def test_respawn_wait_grows_on_early_death_and_resets_when_stable():
    assert monitor.next_wait(10, 1) == 20
    assert monitor.next_wait(400, 1) == monitor.RESPAWN_MAX_S
    assert monitor.next_wait(400, monitor.STABLE_S) == monitor.RESPAWN_S


def test_failed_respawn_is_not_counted(monkeypatch):
    class Dead:
        exitcode = -9

        def is_alive(self):
            return False

    stop, calls = threading.Event(), []

    def spawn(restarts=0):
        calls.append(restarts)
        if len(calls) < 3:
            raise OSError("no process")
        stop.set()
        return Dead()

    monkeypatch.setattr(monitor, "_process", Dead())
    monkeypatch.setattr(monitor, "spawn", spawn)
    monkeypatch.setattr(monitor, "CHECK_S", 0.001)
    monkeypatch.setattr(monitor, "RESPAWN_S", 0.001)
    monkeypatch.setattr(monitor, "RESPAWN_MAX_S", 0.002)
    monitor.watch(stop)
    assert calls == [1, 1, 1]


def test_stop_is_safe_after_watcher_failed_to_start(monkeypatch):
    terminated = []
    process = SimpleNamespace(is_alive=lambda: bool(not terminated), terminate=lambda: terminated.append(1), join=lambda timeout: None, kill=lambda: None)
    monkeypatch.setattr(monitor, "spawn", lambda restarts=0: process)
    monkeypatch.setattr(monitor, "_watcher", None)
    monkeypatch.setattr("app.Server_Monitor_Bot.hooks.install.install", lambda *a: True)
    monkeypatch.setattr("app.Server_Monitor_Bot.hooks.install.uninstall", lambda: None)
    monkeypatch.setenv("AI_DB_URL", "mysql://u:p@h:3306/ai_dev")

    def refuse(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", refuse)
    assert monitor.start() is False
    assert monitor._process is process and monitor._watcher is None
    monkeypatch.undo()
    monitor.stop()
    assert terminated == [1] and monitor._process is None


def test_install_stops_on_bad_db_name_before_touching_stores(monkeypatch, capsys):
    monkeypatch.setenv("AI_DB_URL", "mysql://u:secretpw@h:3306/monitor?ssl=required")

    def touched(*args, **kwargs):
        raise AssertionError("store touched")

    monkeypatch.setattr("app.Server_Monitor_Bot.store.vector.VectorDB.__init__", touched)
    monkeypatch.setattr("app.Server_Monitor_Bot.store.mysql.MySQLDB.from_url", touched)
    assert install.main([]) == 1
    captured = capsys.readouterr()
    assert "ai_<local|dev|stg|prod>" in captured.err and "secretpw" not in captured.err + captured.out


def test_image_tag_outside_ecs():
    assert runner.image_tag({}) == "local"
    k8s = {"KUBERNETES_SERVICE_HOST": "10.0.0.1"}
    assert runner.image_tag(k8s, "fastapi-784b49f79-bdkgs") == "k8s-784b49f79"
    assert runner.image_tag(k8s, "fastapi") == "k8s"
