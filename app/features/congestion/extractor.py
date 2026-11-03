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
[세는 글]
지금 현장에서 직접 본 혼잡만 센다. 질문, 희망, 예측, 전언은 세지 않는다(단계 null, causes []).
과거 관찰("어제", "10분 전에", "아까", "방금 봤을 때")은 지금까지 이어진다는 근거가 없으면
단계 null이다. 과거와 현재가 함께 있으면 현재로 판단한다.
역·정류장 안이나 주변 가게의 카페 자리, 식당 웨이팅, 매장 줄은 스팟 혼잡이 아니다(단계 null, causes []).

[단계]
congestion_level은 LOW, MEDIUM, HIGH 또는 null이다.
LOW: "한산해요", "줄 없어요", "바로 탔어요", "사람은 있는데 여유 있어요".
MEDIUM: "사람 많아요", "줄 길어요", "사람 많지만 이동할 만해요". 정도 표현이 없어도 지금 줄을 서거나
기다리는 중이면 MEDIUM이다("지금 줄 서 있어요", "10분째 기다리는 중").
HIGH: "사람 엄청 많아요", "줄 엄청 길어요", "못 움직여요", 혼잡·만석 때문에 못 탐("두 대 그냥 보냈어요").
혼잡 표현에 "엄청", "너무" 같은 강조가 붙으면 이동이 괜찮다고 해도 HIGH다.
도로: 소통 원활은 LOW, 서행이지만 신호 한 번에 통과는 MEDIUM, 신호를 여러 번 기다리거나 거의 멈춤·
"엄청 막혀요"는 HIGH다.
애매하거나 같은 글 안에서 단계가 엇갈리면 null이다. "괜찮아요", "사람이 줄었어요"만으로는 정하지 않는다.
혼잡 정도를 모른다고 쓰면 null이다. 사고·지연 사실만으로 단계를 올리지 않는다.

[원인]
causes는 아래 코드의 배열이며 없으면 []다. 단계와 따로 판단한다. 원인만 있고 단계가 없으면
단계는 null, 원인은 넣는다.
원인이 혼잡·대기에 영향을 준다는 관계가 글에 쓰여 있을 때만 넣는다. 사실만 말하면 넣지 않는다.
"지금 비가 많이 와요" → []. "폭우 때문에 대기줄이 길어졌어요" → WEATHER.
"신호기 고장 때문에 사람들이 기다려요" → MEDIUM, BREAKDOWN.
부정한 원인, 추측한 원인("행사가 있나 봐요"), 시간대만으로 추정한 RUSH_HOUR는 넣지 않는다. 코드가 애매하면 그 원인은 넣지 않는다.
원인으로 고장을 넣을 때 늦음·안 옴도 쓰여 있으면 DELAY도 넣는다.
예외: 차량 도로 정체("차가 막혀요", "도로가 막혀서 버스가 안 와요")가 쓰여 있으면 관계가 없어도
ROAD_TRAFFIC을 넣는다. "차가 잘 빠져요"면 넣지 않는다.
코드: RUSH_HOUR(출퇴근), EVENT(행사·공연·경기), ACCIDENT(사고), BREAKDOWN(차량·설비 고장),
DELAY(운행 지연), SERVICE_DISRUPTION(중단·통제·우회), WEATHER(폭우·폭설·태풍·한파),
ROAD_TRAFFIC(도로 정체), CONSTRUCTION(공사·시설 작업), DEMONSTRATION(집회·시위·행진).
"""


async def call_api_model(prompt: str) -> str:
    # 모델 호출 파일이 BundleDecision을 읽으므로 순환 import를 피한다.
    from . import model_client

    output = await model_client.complete_json(prompt)
    if not isinstance(output, str):
        raise ModelOutputInvalid("api model did not return text")
    return output
