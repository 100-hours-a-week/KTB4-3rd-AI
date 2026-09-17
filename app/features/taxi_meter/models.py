from pydantic import BaseModel


class TaxiMeterRequest(BaseModel):
    image_URL: str
    ride_channel_id: str


class TaxiMeterResponse(BaseModel):
    cost: int
