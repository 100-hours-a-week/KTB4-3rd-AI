"""관련성 선별 폴백: Modal A10에 올린 SemIf r2(Qwen3.5-4B + LoRA)를 부른다(D33).

순서는 service.py가 정한다: Decisions → SemIf → 키워드. 이 파일은 SemIf 입력을 만들고 원격으로 부르기만 한다.
배포는 modal_app.py, 호출 방식은 이미지 기능(app/llm/providers/modal_gpu.py)과 같다:
spawn 뒤 SEMIF_WAIT초까지만 기다리고, 넘기면 다음 방식(키워드)으로 넘긴다. 원격 호출은 계속 돌아 컨테이너를 데운다.
"""

import asyncio
import os
import time
from typing import Any

from ...core.exceptions import ModelNotReady, UpstreamError
from ...core.logging import get_logger, log_import_error
from .config import SEMIF_WAIT
from .decisions import INSTRUCTIONS
from .preprocessor import PreparedSpot
from .relevance import RelevanceOutputInvalid, SignalKey

logger = get_logger(__name__)

# modal_app.py의 이름과 같아야 한다(테스트로 확인).
APP = "congestion-semif"
CLS = "SemifModel"
TOKEN_ENV = ("MODAL_TOKEN_ID_2", "MODAL_TOKEN_SECRET_2")  # _2: 혼잡도 Modal 계정(.env.example)

YES = {"id": "yes", "description": "기준에 해당한다"}
NO = {"id": "no", "description": "기준에 해당하지 않는다"}


def semif_rows(prepared: PreparedSpot) -> list[dict[str, Any]]:
    """후보마다 SemIf 입력 한 줄. 모든 줄이 같은 state를 써서 서버가 스팟 글을 한 번만 읽는다(shared).

    학습·평가(.ai/tools)와 같은 형식이어야 한다: 분류 기준 = Decisions 지시문, 질문 q2, 보기 예/아니오.
    state 끝의 "끝" 필드는 shared 모드의 앞부분 토큰 검사가 괄호에서 갈라지지 않게 하는 표식이다.
    """
    data = {
        "window_end": prepared.window_end.isoformat(),
        "post_bundles": [post.model_dump(mode="json") for post in prepared.post_bundles],
    }
    state = {"분류 기준": INSTRUCTIONS, "분류할 데이터": data, "끝": "끝"}
    return [
        {
            "id": f"{c.signal_type}_{c.signal_id}",
            "state": state,
            "question": f"{c.signal_type}_id={c.signal_id}는 위 기준에 해당하는 글인가?",
            "options": [YES, NO],
        }
        for c in prepared.candidates
    ]


class SemifRelevanceScorer:
    """Modal에 배포한 SemifModel.score를 불러 후보별 관련 확률을 낸다.

    토큰이 없으면(v2 기본) 부르지 않고 ModelNotReady를 낸다. Modal 조회가 한 번 실패하면 재시작 전까지 쓰지 않는다
    (이미지 기능과 같은 규칙). 두 경우 모두 service.py가 키워드로 넘긴다.
    """

    def __init__(self, *, remote: Any = None, wait: float = SEMIF_WAIT) -> None:
        self._remote = remote
        self._wait = wait
        self._broken = False
        self._lock = asyncio.Lock()

    async def _load(self) -> Any:
        if self._remote is not None:
            return self._remote
        if self._broken:
            raise ModelNotReady()
        token_id, token_secret = (os.environ.get(name, "") for name in TOKEN_ENV)
        if not token_id or not token_secret:
            raise ModelNotReady()
        async with self._lock:
            if self._remote is None:
                try:
                    import modal  # type: ignore[import-not-found]
                except ImportError as exc:
                    log_import_error(logger, "semif", ("modal",), exc)
                    self._broken = True
                    raise ModelNotReady() from exc

                def lookup() -> Any:
                    client = modal.Client.from_credentials(token_id, token_secret)
                    return modal.Cls.from_name(APP, CLS, client=client).hydrate(client)()

                try:
                    self._remote = await asyncio.to_thread(lookup)
                except Exception as exc:
                    self._broken = True
                    logger.error(
                        "congestion semif lookup failed: reason=%s hint=MODAL_TOKEN_ID_2·MODAL_TOKEN_SECRET_2 "
                        "값과 그 계정으로 modal deploy -m app.features.congestion.modal_app 했는지 확인",
                        type(exc).__name__,
                    )
                    raise ModelNotReady() from exc
        return self._remote

    async def __call__(self, prepared: PreparedSpot) -> dict[SignalKey, float | None]:
        if not prepared.candidates:
            return {}
        remote = await self._load()
        rows = semif_rows(prepared)
        started = time.monotonic()
        try:
            call = await remote.score.spawn.aio(rows)
            probs = await call.get.aio(timeout=self._wait)
        except Exception as exc:
            name = type(exc).__name__
            reason = "timeout" if "Timeout" in name else "remote_error"
            logger.warning(
                "congestion semif call failed: reason=%s error=%s ms=%s",
                reason, name, int((time.monotonic() - started) * 1000),
            )
            raise UpstreamError() from exc
        if not isinstance(probs, dict) or set(probs) != {row["id"] for row in rows}:
            raise RelevanceOutputInvalid("semif ids do not match candidates", reason="id_mismatch")
        logger.info(
            "congestion semif call done: candidates=%s ms=%s",
            len(rows), int((time.monotonic() - started) * 1000),
        )
        return {
            (c.signal_type, c.signal_id): probs[f"{c.signal_type}_{c.signal_id}"]
            for c in prepared.candidates
        }
