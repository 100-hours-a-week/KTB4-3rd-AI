# POST /receipt 요청을 내부 큐(app.state.scheduler)에 넣고 결과를 기다린다.
from fastapi import APIRouter, Request

from .models import ReceiptRequest, ReceiptResponse

router = APIRouter()


@router.post("/receipt", response_model=ReceiptResponse)
async def receipt(req: ReceiptRequest, request: Request) -> ReceiptResponse:
    return ReceiptResponse(**await request.app.state.scheduler.submit("receipt", req.image_URL))
