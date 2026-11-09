"""SemIf 선별 폴백(D33): 입력 형식, 원격 호출 실패 처리, Decisions → SemIf → 키워드 순서."""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import ModelNotReady, UpstreamError
from app.features.congestion import semif
from app.features.congestion.decisions import INSTRUCTIONS
from app.features.congestion.models import (
    CongestionAnalysisMessage,
    CongestionSpotInput,
)
from app.features.congestion.preprocessor import PreparedSpot, prepare_spot
from app.features.congestion.relevance import RelevanceOutputInvalid, RelevancePolicy

SPOT = {
    "spot_id": 7,
    "post_bundles": [
        {
            "post_id": 1, "context_only": False, "title": "정류장", "content": "줄이 길어요",
            "author_key": "a", "created_at": "2026-10-12T11:55:00+09:00",
            "comments": [{"comment_id": 1, "content": "카페 웨이팅 실화", "author_key": "b",
                          "created_at": "2026-10-12T11:56:00+09:00"}],
        }
    ],
}
WINDOW_END = "2026-10-12T12:00:00+09:00"


@pytest.fixture
def prepared() -> PreparedSpot:
    return prepare_spot(CongestionSpotInput.model_validate(SPOT), datetime.fromisoformat(WINDOW_END))


class FakeRemote:
    """remote.score.spawn.aio(rows) → call.get.aio(timeout=) 모양만 흉내 낸다."""

    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.rows = None
        self.timeout = None

        async def spawn(rows):
            self.rows = rows

            async def get(timeout):
                self.timeout = timeout
                if error is not None:
                    raise error
                return result(rows) if callable(result) else result

            return SimpleNamespace(get=SimpleNamespace(aio=get))

        self.score = SimpleNamespace(spawn=SimpleNamespace(aio=spawn))


def test_rows_match_training_format(prepared: PreparedSpot) -> None:
    rows = semif.semif_rows(prepared)
    assert [r["id"] for r in rows] == ["post_1", "comment_1"]
    assert rows[0]["state"] is rows[1]["state"]  # 같은 state → 서버가 스팟 글을 한 번만 읽는다
    assert list(rows[0]["state"]) == ["분류 기준", "분류할 데이터", "끝"]
    assert rows[0]["state"]["분류 기준"] == INSTRUCTIONS
    assert rows[0]["state"]["끝"] == "끝"
    assert rows[1]["question"] == "comment_id=1는 위 기준에 해당하는 글인가?"
    assert rows[0]["options"] == [{"id": "yes", "description": "기준에 해당한다"},
                                  {"id": "no", "description": "기준에 해당하지 않는다"}]


def test_names_match_deploy_file() -> None:
    text = (Path(semif.__file__).parent / "modal_app.py").read_text(encoding="utf-8")
    assert re.search(r'^APP = "(.+)"$', text, re.MULTILINE).group(1) == semif.APP
    assert re.search(r'^CLS = "(.+)"$', text, re.MULTILINE).group(1) == semif.CLS
    assert f"class {semif.CLS}:" in text


@pytest.mark.asyncio
async def test_missing_token_skips_without_modal(prepared: PreparedSpot, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in semif.TOKEN_ENV:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(semif.SemifUnavailable) as caught:
        await semif.SemifRelevanceScorer()(prepared)
    assert caught.value.reason == "missing_key"


@pytest.mark.asyncio
async def test_scores_by_candidate(prepared: PreparedSpot) -> None:
    remote = FakeRemote(lambda rows: {r["id"]: 0.9 if r["id"] == "post_1" else 0.05 for r in rows})
    scores = await semif.SemifRelevanceScorer(remote=remote, wait=3.0)(prepared)
    assert scores == {("post", 1): 0.9, ("comment", 1): 0.05}
    assert remote.timeout == 3.0


@pytest.mark.asyncio
async def test_timeout_becomes_upstream_error(prepared: PreparedSpot, caplog: pytest.LogCaptureFixture) -> None:
    class TimeoutError_(Exception):
        pass

    TimeoutError_.__name__ = "TimeoutError"
    remote = FakeRemote(error=TimeoutError_("slow"))
    with pytest.raises(UpstreamError):
        await semif.SemifRelevanceScorer(remote=remote)(prepared)
    assert "congestion semif call failed: reason=timeout" in caplog.text
    assert "줄이 길어요" not in caplog.text


@pytest.mark.asyncio
async def test_id_mismatch_is_invalid(prepared: PreparedSpot) -> None:
    remote = FakeRemote({"post_1": 0.9})
    with pytest.raises(RelevanceOutputInvalid):
        await semif.SemifRelevanceScorer(remote=remote)(prepared)



def _fake_modal(monkeypatch: pytest.MonkeyPatch, fails: int, remote: FakeRemote) -> dict[str, int]:
    """modal.Cls.from_name이 처음 fails번 실패하는 가짜 modal 모듈. 조회 횟수를 센다."""
    for name in semif.TOKEN_ENV:
        monkeypatch.setenv(name, "token-value")
    seen = {"lookups": 0}

    def from_name(app: str, cls: str, client: object) -> SimpleNamespace:
        seen["lookups"] += 1
        if seen["lookups"] <= fails:
            raise RuntimeError("lookup down")
        return SimpleNamespace(hydrate=lambda c: (lambda: remote))

    fake = SimpleNamespace(
        Client=SimpleNamespace(from_credentials=lambda token_id, token_secret: object()),
        Cls=SimpleNamespace(from_name=from_name),
    )
    monkeypatch.setitem(sys.modules, "modal", fake)
    return seen


@pytest.mark.asyncio
async def test_lookup_failure_waits_before_retry(
    prepared: PreparedSpot, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    seen = _fake_modal(monkeypatch, fails=1, remote=FakeRemote({"post_1": 0.9, "comment_1": 0.1}))
    scorer = semif.SemifRelevanceScorer(retry_after=600)
    for _ in range(2):
        with pytest.raises(semif.SemifUnavailable) as caught:
            await scorer(prepared)
        assert caught.value.reason == "not_ready"
    assert seen["lookups"] == 1  # 기다리는 동안은 다시 조회하지 않는다
    assert "congestion semif lookup failed: reason=RuntimeError retry_in=600s" in caplog.text
    assert "token-value" not in caplog.text


@pytest.mark.asyncio
async def test_lookup_retries_after_wait(prepared: PreparedSpot, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake_modal(monkeypatch, fails=1, remote=FakeRemote({"post_1": 0.9, "comment_1": 0.1}))
    scorer = semif.SemifRelevanceScorer(retry_after=0)
    with pytest.raises(ModelNotReady):
        await scorer(prepared)
    assert await scorer(prepared) == {("post", 1): 0.9, ("comment", 1): 0.1}
    assert seen["lookups"] == 2

def _message() -> CongestionAnalysisMessage:
    return CongestionAnalysisMessage.model_validate(
        {"event_id": "semif-test", "schema_version": 1, "window_end": WINDOW_END, **SPOT}
    )


async def _complete(prompt: str) -> str:
    targets = json.loads(prompt.split("입력 JSON:\n", 1)[1])["targets"]
    return json.dumps({"signals": [{**t, "congestion_level": "MEDIUM", "causes": []} for t in targets]})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("semif_scorer", "method", "semif_reason", "targets"),
    [
        # SemIf가 카페 댓글을 0.05로 거르면 원글만 판정한다.
        (lambda: semif.SemifRelevanceScorer(remote=FakeRemote({"post_1": 0.9, "comment_1": 0.05})),
         "semif", "ok", 1),
        # SemIf가 실패하면 키워드: "줄"·"웨이팅"이 없는 댓글은 거르고 "줄"이 있는 원글은 남긴다.
        (lambda: semif.SemifRelevanceScorer(remote=FakeRemote(error=RuntimeError("down"))),
         "keyword", "remote_error", None),
        (lambda: AsyncMock(side_effect=ModelNotReady()), "keyword", "missing_key", None),
        # 토큰은 있지만 조회 실패 뒤 기다리는 중이면 키 문제와 구분한다.
        (lambda: AsyncMock(side_effect=semif.SemifUnavailable("not_ready")), "keyword", "not_ready", None),
    ],
)
async def test_decisions_then_semif_then_keyword(
    semif_scorer, method: str, semif_reason: str, targets: int | None, caplog: pytest.LogCaptureFixture,
) -> None:
    from app.features.congestion.service import LLMCongestionAnalyzer

    complete = AsyncMock(side_effect=_complete)
    analyzer = LLMCongestionAnalyzer(
        score=AsyncMock(side_effect=UpstreamError()), policy=RelevancePolicy(0.2, 0.8),
        complete=complete, semif=semif_scorer(),
    )
    result = await analyzer.analyze(_message())
    logs = [r.getMessage() for r in caplog.records if r.getMessage().startswith("congestion relevance fallback")]
    assert len(logs) == 1
    assert f"semif={semif_reason} method={method} candidates=2" in logs[0]
    if targets is not None:
        sent = json.loads(complete.call_args.args[0].split("입력 JSON:\n", 1)[1])["targets"]
        assert len(sent) == targets
    assert result.congestion_level in ("MEDIUM", "UNKNOWN")
