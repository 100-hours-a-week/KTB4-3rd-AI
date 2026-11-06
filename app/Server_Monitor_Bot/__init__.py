# 서버 모니터링 봇 진입점. start(app): 조건 확인 → 훅 설치 → 분석 프로세스 spawn·감시(죽으면 다시 띄움). 실패해도 서비스는 그대로 기동한다.
import logging
import multiprocessing
import re
import threading
import time

log = logging.getLogger("app.Server_Monitor_Bot")
CHECK_S = 5.0
RESPAWN_S = 10.0
RESPAWN_MAX_S = 600.0
STABLE_S = 300.0
_process = None
_watcher = None
_watch_stop = threading.Event()


def spawn(restarts: int = 0):
    from .runtime.runner import main

    process = multiprocessing.get_context("spawn").Process(target=main, args=(restarts,), name="server-bot-analysis", daemon=True)
    process.start()
    return process


def next_wait(wait: float, lived_s: float) -> float:
    return RESPAWN_S if lived_s >= STABLE_S else min(wait * 2, RESPAWN_MAX_S)


def watch(stop: threading.Event) -> None:
    global _process
    restarts, wait, born = 0, RESPAWN_S / 2, time.monotonic()
    while not stop.wait(CHECK_S):
        if _process is None or _process.is_alive():
            continue
        wait = next_wait(wait, time.monotonic() - born)
        log.warning("analysis died: exitcode=%s restarts=%s respawn_in=%ss", _process.exitcode, restarts, wait)
        if stop.wait(wait):
            return
        try:
            _process = spawn(restarts + 1)
            restarts += 1
        except Exception as exc:
            log.warning("analysis respawn failed: error=%s", type(exc).__name__)
        born = time.monotonic()


def start(app=None) -> bool:
    global _process, _watcher, _watch_stop
    try:
        from .config.settings import db_name, load
        from .hooks.install import install

        settings = load()
        if settings.db_url and not settings.stage:
            name = db_name(settings.db_url)
            log.warning("monitor disabled: reason=db_name db=%s", name if re.fullmatch(r"\w{1,64}", name, re.ASCII) else "?")
        if not settings.enabled:
            return False
        if not install(settings.stage, settings.buffer_dir, settings.section("buffer").get("max_mb", 50)):
            return False
        _process = spawn()
        _watch_stop = threading.Event()
        watcher = threading.Thread(target=watch, args=(_watch_stop,), name="server-bot-watch", daemon=True)
        watcher.start()
        _watcher = watcher
        return True
    except Exception as exc:
        log.warning("monitor disabled: reason=start_failed error=%s", type(exc).__name__)
        return False


def stop(timeout: float = 12.0) -> None:
    global _process, _watcher
    _watch_stop.set()
    if _watcher is not None:
        _watcher.join(5)
    _watcher = None
    try:
        from .hooks.install import uninstall

        uninstall()
    except Exception:
        pass
    if _process is not None and _process.is_alive():
        _process.terminate()
        _process.join(timeout)
        if _process.is_alive():
            _process.kill()
    _process = None
