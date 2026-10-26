# 영수증 요청과 응답 필드를 정의한다.
from pydantic import BaseModel


class ReceiptRequest(BaseModel):
    image_URL: str


class ReceiptResponse(BaseModel):
    success: bool = True
    amount_cost: int
    transfer_info: str
    sender_name: str
    send_time: str | None
