"""스팟의 분류 후보와 해석에 필요한 원문 문맥을 준비한다."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from .models import CongestionPostBundleInput, CongestionSpotInput

WINDOW = timedelta(minutes=20)


@dataclass(frozen=True)
class CandidateSignal:
    """관련성 분류에 넘길 원글·댓글과 원문 식별 정보."""

    signal_type: Literal["post", "comment"]
    signal_id: int
    post_id: int
    title: str | None
    content: str
    author_key: str
    created_at: datetime


@dataclass(frozen=True)
class PreparedSpot:
    """분류 후보와 후속 입력 구성에서 참조할 부모·댓글 원문."""

    spot_id: int
    window_end: datetime
    post_bundles: tuple[CongestionPostBundleInput, ...]
    candidates: tuple[CandidateSignal, ...]


def is_in_window(
    created_at: datetime, as_of: datetime, window: timedelta = WINDOW
) -> bool:
    return as_of - window < created_at <= as_of


def prepare_spot(spot: CongestionSpotInput, window_end: datetime) -> PreparedSpot:
    """문맥용 원글을 제외한 원글과 모든 댓글을 분류 후보로 만든다.

    후보가 있는 묶음의 원문 전체를 보관해 후속 단계에서 부모·댓글 문맥을 참조한다.
    작성자의 반복 제보와 일반 글도 관련성·개별 판정을 거칠 수 있도록 그대로 남긴다.
    """
    post_bundles: list[CongestionPostBundleInput] = []
    candidates: list[CandidateSignal] = []
    for post in spot.post_bundles:
        post_candidates: list[CandidateSignal] = []
        if not post.context_only:
            post_candidates.append(
                CandidateSignal(
                    signal_type="post",
                    signal_id=post.post_id,
                    post_id=post.post_id,
                    title=post.title,
                    content=post.content,
                    author_key=post.author_key,
                    created_at=post.created_at,
                )
            )
        for comment in post.comments:
            post_candidates.append(
                CandidateSignal(
                    signal_type="comment",
                    signal_id=comment.comment_id,
                    post_id=post.post_id,
                    title=None,
                    content=comment.content,
                    author_key=comment.author_key,
                    created_at=comment.created_at,
                )
            )
        if post_candidates:
            post_bundles.append(post)
            candidates.extend(post_candidates)

    return PreparedSpot(
        spot_id=spot.spot_id,
        window_end=window_end,
        post_bundles=tuple(post_bundles),
        candidates=tuple(candidates),
    )


def select_analyzable_bundles(
    spot: CongestionSpotInput, as_of: datetime
) -> list[CongestionPostBundleInput]:
    """기존 묶음별 분석 경로에서 최근 제보가 있는 묶음의 원문을 남긴다."""
    return [
        post
        for post in spot.post_bundles
        if is_in_window(post.created_at, as_of)
        or any(is_in_window(comment.created_at, as_of) for comment in post.comments)
    ]
