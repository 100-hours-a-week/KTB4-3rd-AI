# 영수증 요청과 응답 필드를 정의한다.
from pydantic import BaseModel


class ReceiptRequest(BaseModel):
    image_URL: str
    ride_channel_id: str


class ReceiptResponse(BaseModel):
    success: bool = True
    amount_cost: int
    transfer_info: str
    sender_name: str
    send_time: str
