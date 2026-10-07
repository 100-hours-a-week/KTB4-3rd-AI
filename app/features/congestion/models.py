"""혼잡도 API 계약. 문자열·목록 제한은 결정 기록의 가정을 따른다."""

import re
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


def require_nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must not be blank")
    return value


def require_iso_timestamp(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
        value,
    ):
        raise ValueError("timestamp must be an ISO 8601 string with timezone")
    return value


PositiveId = Annotated[int, Field(strict=True, gt=0)]
ReportCount = Annotated[int, Field(strict=True, ge=0)]
NonBlankText = Annotated[
    str, StringConstraints(strict=True, min_length=1), AfterValidator(require_nonblank)
]
KeyText = Annotated[NonBlankText, Field(max_length=128)]
TitleText = Annotated[NonBlankText, Field(max_length=30)]
PostText = Annotated[NonBlankText, Field(max_length=280)]
CommentText = Annotated[NonBlankText, Field(max_length=280)]
Timestamp = Annotated[AwareDatetime, BeforeValidator(require_iso_timestamp)]
CongestionLevel = Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
CauseCode = Literal[
    "RUSH_HOUR",
    "EVENT",
    "ACCIDENT",
    "BREAKDOWN",
    "DELAY",
    "SERVICE_DISRUPTION",
    "WEATHER",
    "ROAD_TRAFFIC",
    "CONSTRUCTION",
    "DEMONSTRATION",
    "UNKNOWN",
]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CongestionCommentInput(ApiModel):
    comment_id: PositiveId
    content: CommentText
    author_key: KeyText
    created_at: Timestamp


class CongestionPostBundleInput(ApiModel):
    post_id: PositiveId
    title: TitleText
    content: PostText
    author_key: KeyText
    created_at: Timestamp
    comments: Annotated[list[CongestionCommentInput], Field(max_length=100)]


class CongestionSpotInput(ApiModel):
    spot_id: PositiveId
    post_bundles: Annotated[list[CongestionPostBundleInput], Field(max_length=50)]


class CongestionAnalysisRequest(ApiModel):
    request_id: KeyText
    as_of: Timestamp
    spots: Annotated[list[CongestionSpotInput], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def validate_ids_and_size(self) -> "CongestionAnalysisRequest":
        spot_ids: set[int] = set()
        post_ids: set[int] = set()
        comment_ids: set[int] = set()
        for spot in self.spots:
            if spot.spot_id in spot_ids:
                raise ValueError("duplicate spot_id")
            spot_ids.add(spot.spot_id)
            for post in spot.post_bundles:
                if post.created_at > self.as_of:
                    raise ValueError("post created_at cannot be after as_of")
                if post.post_id in post_ids:
                    raise ValueError("duplicate post_id")
                post_ids.add(post.post_id)
                for comment in post.comments:
                    if comment.created_at > self.as_of:
                        raise ValueError("comment created_at cannot be after as_of")
                    if comment.comment_id in comment_ids:
                        raise ValueError("duplicate comment_id")
                    comment_ids.add(comment.comment_id)
                if len(post_ids) + len(comment_ids) > 1000:
                    raise ValueError("at most 1000 posts and comments per request")
        return self


class CongestionCause(ApiModel):
    cause_code: CauseCode
    report_count: ReportCount


class CongestionResult(ApiModel):
    congestion_level: CongestionLevel
    report_count: ReportCount
    causes: list[CongestionCause]


class CongestionSpotSuccess(ApiModel):
    spot_id: PositiveId
    analysis_status: Literal["SUCCESS"] = "SUCCESS"
    result: CongestionResult


class CongestionSpotFailure(ApiModel):
    spot_id: PositiveId
    analysis_status: Literal["FAILED"] = "FAILED"
    result: None = None
    code: Literal["ANALYSIS_FAILED"] = "ANALYSIS_FAILED"
    message: Literal["analysis failed"] = "analysis failed"


CongestionSpotResult = Annotated[
    CongestionSpotSuccess | CongestionSpotFailure,
    Field(discriminator="analysis_status"),
]


class CongestionAnalysisResponse(ApiModel):
    request_id: KeyText
    spots: list[CongestionSpotResult]
