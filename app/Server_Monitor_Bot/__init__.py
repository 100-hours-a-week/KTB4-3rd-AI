# 서버 모니터링 봇 진입점. start(app): 조건 확인 → 훅 설치 → 분석 프로세스 spawn. 실패해도 서비스는 그대로 기동한다.
import logging
import multiprocessing

log = logging.getLogger("app.Server_Monitor_Bot")
_process = None


def start(app=None) -> bool:
    global _process
    try:
        from .config.settings import load
        from .hooks.install import install

        settings = load()
        if not settings.enabled:
            return False
        if not install(settings.stage, settings.buffer_dir, settings.section("buffer").get("max_mb", 50)):
            return False
        from .runtime.runner import main

        ctx = multiprocessing.get_context("spawn")
        _process = ctx.Process(target=main, name="server-bot-analysis", daemon=True)
        _process.start()
        return True
    except Exception as exc:
        log.warning("monitor disabled: reason=start_failed error=%s", type(exc).__name__)
        return False


def stop(timeout: float = 12.0) -> None:
    global _process
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
