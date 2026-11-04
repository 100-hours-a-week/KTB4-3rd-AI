# 분석 프로세스: 일일 작업은 하루 한 번(MySQL 선점), AI_DB_URL 없이 기동해도 예외 없이 끝난다.
import subprocess
import sys
from datetime import datetime
from types import SimpleNamespace

from app.Server_Monitor_Bot.runtime import runner


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
