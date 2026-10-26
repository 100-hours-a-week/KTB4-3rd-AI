# Modal T4 인터페이스. llm/modal_app.py의 pipe 하나로 작업 1건(OCR + openjev)을 원격에서 돌린다.
# 토큰은 MODAL_TOKEN_ID_{slot}, MODAL_TOKEN_SECRET_{slot}에서 읽어 클라이언트를 직접 만든다. _1 이미지, _2 혼잡도로 계정을 나눈다.
# 기동 때 prepare()가 앱을 조회하고, 없으면 그 자리에서 배포한다. 앱 이름에 코드 버전을 붙여 서버와 원격 코드가 항상 같다.
import hashlib
import os
import time
from pathlib import Path
from typing import Any

from ...core.exceptions import ModelNotReady, UpstreamError
from ...core.logging import get_logger, log_import_error
from ..types import ImageBytes

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[2]  # app/
# 원격에서 도는 코드. 이 아래 .py가 바뀌면 VERSION이 바뀌어 새 이름으로 다시 배포한다.
SOURCES = ("llm", "features/taxi_meter", "features/receipt", "core")
APP_BASE = "oj-image-models"
CLS = "OjModels"
# 작업 1건 Modal 대기 상한(초). BE 60초 - 이미지 다운로드 10초 - luna 30초 - 여유 5초.
WAIT = 15
# 원인 이름 → reason. 이름에 Timeout이 들어가면 timeout이다.
REASONS = {
    "AuthError": "auth",
    "NotFoundError": "not_found",
    "ResourceExhaustedError": "quota",
    "PermissionDeniedError": "permission",
}


def version(root: Path = ROOT) -> str:
    # 경로 순으로 정렬해 (상대 경로 + 내용)을 해시한다. 파일을 읽는 순서와 무관하게 같은 값이다.
    digest = hashlib.sha256()
    for path in sorted(p for d in SOURCES for p in (root / d).rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


VERSION = version()
APP = f"{APP_BASE}-{VERSION}"


class ModalEngine:
    def __init__(self, slot: str = "1") -> None:
        self.slot = slot
        self.remote = None
        # idle → deploying → ready, 실패하면 down. 이벤트 루프 하나에서만 바뀌어 잠그지 않는다.
        # ponytail: down은 재시작 전까지 고정이다. 중간 복구가 필요하면 일정 시간 뒤 prepare()를 다시 부른다.
        self.state = "idle"

    def set(self, state: str) -> None:
        if state != self.state:
            logger.info("modal state changed: from=%s to=%s", self.state, state)
            self.state = state

    async def lookup(self, modal: Any, client: Any) -> Any:
        # hydrate로 토큰과 배포된 앱을 지금 확인한다.
        cls = modal.Cls.from_name(APP, CLS, client=client)
        await cls.hydrate.aio(client)
        return cls()

    async def prepare(self) -> None:
        try:
            import modal
        except ImportError as exc:
            log_import_error(logger, "modal", ("modal",), exc)
            return self.set("down")
        try:
            # from_credentials는 서버에 접속한다. 동기 호출은 이벤트 루프를 막으므로 .aio로 기다린다.
            client = await modal.Client.from_credentials.aio(
                os.environ.get(f"MODAL_TOKEN_ID_{self.slot}", ""), os.environ.get(f"MODAL_TOKEN_SECRET_{self.slot}", "")
            )
            self.remote = await self.lookup(modal, client)
            return self.set("ready")
        except Exception as exc:
            if not isinstance(exc, modal.exception.NotFoundError):
                # 토큰이 없거나 틀리면 여기서 실패한다. 재시작 전까지 GPU 경로는 luna로 넘어간다.
                logger.error("modal lookup failed: reason=%s slot=%s hint=MODAL_TOKEN_ID_%s·MODAL_TOKEN_SECRET_%s 값과 그 계정으로 modal deploy -m app.llm.modal_app 했는지 확인", type(exc).__name__, self.slot, self.slot, self.slot)
                return self.set("down")
        # 이 버전의 앱이 없다. 첫 빌드는 몇 분 걸리고, 그동안 pipe는 바로 ModelNotReady(→ luna)다.
        self.set("deploying")
        logger.info("modal deploy started: app=%s", APP)
        started = time.monotonic()
        try:
            from .. import modal_app

            await modal_app.app.deploy.aio(name=APP, client=client)
            self.remote = await self.lookup(modal, client)
        except Exception as exc:
            logger.error("modal deploy failed: app=%s reason=%s hint=Modal 대시보드의 %s 배포·이미지 빌드 로그와 크레딧 확인. 고친 뒤 서버 재시작", APP, type(exc).__name__, APP)
            return self.set("down")
        logger.info("modal deploy done: app=%s ms=%s", APP, int((time.monotonic() - started) * 1000))
        self.set("ready")

    async def pipe(self, kind: str, image: ImageBytes) -> dict | None:
        # 배포 중·실패는 기다리지 않는다. 상태 로그는 set()에서 바뀔 때만 남긴다.
        if self.state != "ready":
            raise ModelNotReady()
        started = time.monotonic()
        try:
            call = await self.remote.pipe.spawn.aio(kind, image.content, image.media_type)
            # WAIT를 넘기면 luna로 넘어가고, 원격 호출은 계속 돌아 컨테이너를 데운다.
            return await call.get.aio(timeout=WAIT)
        except Exception as exc:
            name = type(exc).__name__
            reason = "timeout" if "Timeout" in name else REASONS.get(name, "remote_error")
            ms = int((time.monotonic() - started) * 1000)
            logger.error("modal call failed: method=%s reason=%s error=%s ms=%s", "pipe", reason, name, ms)
            raise UpstreamError() from exc
