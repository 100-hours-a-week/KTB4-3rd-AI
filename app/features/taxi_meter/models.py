# 미터기 요청과 응답 필드를 정의한다.
from pydantic import BaseModel


class TaxiMeterRequest(BaseModel):
    image_URL: str
    ride_channel_id: str


class TaxiMeterResponse(BaseModel):
    success: bool = True
    cost: int
