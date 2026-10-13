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

from .schema_examples import REQUEST_EXAMPLE, RESPONSE_EXAMPLE


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


def require_window_minutes(value: int) -> int:
    if value not in (10, 60):
        raise ValueError("window_minutes must be 10 or 60")
    return value


PositiveId = Annotated[int, Field(strict=True, gt=0)]
ReportCount = Annotated[int, Field(strict=True, ge=0)]
NonBlankText = Annotated[
    str, StringConstraints(strict=True, min_length=1), AfterValidator(require_nonblank)
]
KeyText = Annotated[NonBlankText, Field(max_length=128)]
AuthorKey = Annotated[
    KeyText,
    Field(
        description=(
            "Spring이 제공하는 안정적인 작성자 식별 키. 같은 작성자는 원글·댓글 및 요청 간에 "
            "같은 키를 사용해야 작성자별 중복 집계가 가능하다. member_4821은 가상 예시이며 "
            "실제 생성 방식·형식은 Spring과 합의가 필요하다."
        ),
        examples=["member_4821"],
    ),
]
TitleText = Annotated[NonBlankText, Field(max_length=30)]
PostText = Annotated[NonBlankText, Field(max_length=280)]
CommentText = Annotated[NonBlankText, Field(max_length=280)]
Timestamp = Annotated[AwareDatetime, BeforeValidator(require_iso_timestamp)]
WindowMinutes = Annotated[
    int,
    Field(strict=True, json_schema_extra={"enum": [10, 60]}),
    AfterValidator(require_window_minutes),
]
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
    author_key: AuthorKey
    created_at: Timestamp


class CongestionPostBundleInput(ApiModel):
    post_id: PositiveId
    title: TitleText
    content: PostText
    author_key: AuthorKey
    created_at: Timestamp
    comments: Annotated[list[CongestionCommentInput], Field(max_length=100)]


class CongestionSpotInput(ApiModel):
    spot_id: PositiveId
    post_bundles: Annotated[list[CongestionPostBundleInput], Field(max_length=50)]


class CongestionAnalysisRequest(ApiModel):
    model_config = ConfigDict(json_schema_extra={"examples": [REQUEST_EXAMPLE]})

    request_id: KeyText = Field(
        description=(
            "Spring이 부여하는 요청 추적용 식별자. 응답에 그대로 반환한다. "
            "UUID는 가상 예시이며 UUID 형식을 강제하지 않는다. 실제 생성 방식은 Spring 미합의다."
        ),
        examples=[REQUEST_EXAMPLE["request_id"]],
    )
    as_of: Timestamp = Field(
        description="Spring이 지정한 공통 분석 기준 시각. 시간대가 있는 ISO 8601 문자열이다.",
        examples=[REQUEST_EXAMPLE["as_of"]],
    )
    window_minutes: WindowMinutes = Field(
        description="Spring이 적용한 집계 창(분): 출퇴근 10, 그 외 60",
        examples=[10, 60],
    )
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


class CongestionResultWithSummary(CongestionResult):
    """코드 집계가 끝난 뒤 생성한 요약을 포함하는 성공 응답."""

    summary: NonBlankText


class CongestionSpotSuccess(ApiModel):
    spot_id: PositiveId
    analysis_status: Literal["SUCCESS"] = "SUCCESS"
    result: CongestionResultWithSummary


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
    model_config = ConfigDict(json_schema_extra={"examples": [RESPONSE_EXAMPLE]})

    request_id: KeyText = Field(
        description="요청에서 받은 request_id를 그대로 반환한다.",
        examples=[RESPONSE_EXAMPLE["request_id"]],
    )
    spots: list[CongestionSpotResult]
