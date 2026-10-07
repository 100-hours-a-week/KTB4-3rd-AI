"""시간 범위에 따라 분석할 게시글 묶음을 고른다."""

from datetime import datetime, timedelta

from .models import CongestionPostBundleInput, CongestionSpotInput

WINDOW = timedelta(minutes=20)


def is_in_window(created_at: datetime, as_of: datetime) -> bool:
    return as_of - WINDOW < created_at <= as_of


def select_analyzable_bundles(
    spot: CongestionSpotInput, as_of: datetime
) -> list[CongestionPostBundleInput]:
    """오래된 부모는 최근 댓글이 있을 때만 원문 전체를 문맥으로 남긴다."""
    return [
        post
        for post in spot.post_bundles
        if is_in_window(post.created_at, as_of)
        or any(is_in_window(comment.created_at, as_of) for comment in post.comments)
    ]
