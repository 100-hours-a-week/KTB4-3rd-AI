"""스팟 판정에 쓰는 모델 출력 타입·판정 규칙·호출 함수를 제공한다."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from .aggregator import Stage
from .models import CauseCode, PositiveId

SignalType = Literal["post", "comment"]


class ModelOutputInvalid(ValueError):
    """LLM 출력 형식이나 요청한 제보 ID 대응이 올바르지 않다."""


class SignalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_type: SignalType
    signal_id: PositiveId
    # 개별 신호의 단계. null은 내부용이며 응답에는 나가지 않는다
    congestion_level: Stage | None
    causes: list[CauseCode]

    @field_validator("causes")
    @classmethod
    def unique_causes(cls, causes: list[CauseCode]) -> list[CauseCode]:
        if len(causes) != len(set(causes)):
            raise ValueError("duplicate cause code")
        return causes


class BundleDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signals: list[SignalDecision]


@dataclass(frozen=True)
class AssessedSignal:
    signal_type: SignalType
    signal_id: int
    author_key: str
    created_at: datetime
    congestion_level: Stage | None
    causes: frozenset[CauseCode]


JUDGMENT_RULES = """\
[세는 글]로 셀지 먼저 정하고, 세는 글이면 [단계]와 [원인]을 따로 정한다.

[세는 글]
지금 현장에서 직접 보거나 겪은 혼잡·운행 상황만 센다. 질문, 희망, 예측, 전언, 지난 일은 세지 않는다.
역·정류장 안내방송이나 안내문으로 확인한 운행 정보는 전언으로 보지 않는다.
지난 일("어제", "10분 전에", "아까 봤을 때")은 지금까지 이어진다는 근거가 없으면 세지 않는다.
한 글에 세지 않는 부분과 지금 직접 본 내용이 함께 있으면 직접 본 내용으로 판단한다
("지금 줄이 정류장 끝까지인데 원래 이래요?" → MEDIUM). 세지 않는 부분만 있으면 단계 null, causes []다.
교통수단을 타거나 역·정류장·도로를 지나는 일과 관계없는 줄과 붐빔은 스팟 혼잡이 아니다(단계 null, causes []).
예: 가게·카페·식당·팝업스토어 줄, 건물 엘리베이터 줄. 이런 줄이 출구 앞까지 이어져도 마찬가지다.

[단계]
congestion_level은 LOW, MEDIUM, HIGH 또는 null이다.
LOW: "한산해요", "줄 없어요", "바로 탔어요", "사람은 있는데 여유 있어요".
MEDIUM: "사람 많아요", "줄 길어요", "사람 많지만 이동할 만해요". 정도 표현이 없어도 지금 줄을 서거나
기다리는 중이면 MEDIUM이다("지금 줄 서 있어요", "10분째 기다리는 중").
"줄 짧아요", "크게 붐비진 않아요"처럼 정도를 낮춰 말해도 기다리는 사람이 있으면 MEDIUM이다.
HIGH: "사람 엄청 많아요", "줄 엄청 길어요", "못 움직여요", "승강장이 사람으로 꽉 찼어요", "줄 끝이 안 보여요",
혼잡·만석 때문에 못 탐("두 대 그냥 보냈어요").
혼잡 표현에 "엄청", "너무" 같은 강조가 붙으면 HIGH다.
도로: 소통 원활은 LOW, 서행이지만 신호 한 번에 통과는 MEDIUM, 신호를 여러 번 기다리거나 거의 멈춤·
"엄청 막혀요"는 HIGH다.
한 글이 서로 다른 곳의 단계를 함께 말하면 null이다. 그 밖에 근거가 여럿이면 HIGH 근거 → 혼잡 표현(많다·길다) →
바로 탐·지나감·한산 → 기다리는 중 순으로 먼저 맞는 것을 따른다. 맞는 것이 없거나 애매하면 null이다
(붐비는지 아닌지 모른다고만 쓴 글 포함). "괜찮아요", "사람이 줄었어요"만으로는 정하지 않는다.
사고·지연 사실만으로 단계를 올리지 않는다.

[원인]
causes는 아래 코드의 배열이며 없으면 []다. 단계와 따로 판단한다. 단계가 null이어도 아래 조건에 맞으면
원인은 넣는다.
원인이 혼잡·대기에 영향을 준다는 관계가 글에 쓰여 있을 때만 넣는다(아래 예외 두 가지만 빼고).
사실만 말하면 넣지 않는다.
"신호기 고장 때문에 사람들이 기다려요" → MEDIUM, SERVICE_ISSUE.
글쓴이가 원인을 혼잡과 이어서 쓰면 "~라 그런지", "~는 듯", "~나 봐요"처럼 짐작하는 말투여도 넣는다.
"출구를 막아놨나?"처럼 묻기만 한 원인과 부정한 원인은 넣지 않는다. 코드가 애매하면 그 원인은 넣지 않는다.
글쓴이가 출퇴근·등하교 시간이나 인파를 혼잡과 함께 쓰면 RUSH_HOUR다. 작성 시각만 그 시간대면 넣지 않는다.
늦어진 이유가 운행 장애면 SERVICE_ISSUE만, 그 밖의 이유이거나 이유가 없으면
DELAY_ISSUE다("열차가 안 와서 10분째 기다려요" → MEDIUM, DELAY_ISSUE).
만석이라 못 타거나 택시가 안 잡히는 것은 혼잡 정도(단계)로만 본다. 이것만으로 원인을 넣지 않는다.
예외: 차량 도로 정체("차가 막혀요", "도로가 막혀서 버스가 안 와요")가 쓰여 있으면 관계가 없어도
DELAY_ISSUE를 넣는다. "차가 잘 빠져요"면 넣지 않는다.
예외: 지금 일어난 교통사고를 직접 제보하면("여기 차 사고 났어요") 혼잡 서술이 없어도 ACCIDENT를 넣는다.
"사고 났대요" 같은 전언은 넣지 않는다.
코드: RUSH_HOUR(출퇴근·등하교 인파), EVENT(행사·공연·경기), ACCIDENT(교통사고),
SERVICE_ISSUE(운행 장애: 고장·점검·장애·운행 중단처럼 운영 쪽 문제로 정상 운행이 안 됨),
DELAY_ISSUE(지연·정체: 운행 장애가 아닌 이유로 평소보다 오래 걸림. 도로 정체, 도로 공사, 집회·행진,
통제·우회, 이유 없는 지연).
"""


async def call_api_model(prompt: str) -> str:
    # 모델 호출 파일이 BundleDecision을 읽으므로 순환 import를 피한다.
    from . import model_client

    output = await model_client.complete_json(prompt)
    if not isinstance(output, str):
        raise ModelOutputInvalid("api model did not return text")
    return output
