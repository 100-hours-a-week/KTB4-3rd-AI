# POST /receipt 요청을 main.run에 연결한다.
from fastapi import APIRouter

from .main import run
from .models import ReceiptRequest, ReceiptResponse

router = APIRouter()


@router.post("/receipt", response_model=ReceiptResponse)
def receipt(req: ReceiptRequest) -> ReceiptResponse:
    return ReceiptResponse(**run(req.image_URL))
