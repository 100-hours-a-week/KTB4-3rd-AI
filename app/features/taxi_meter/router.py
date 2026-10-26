# POST /taxi_meter 요청을 내부 큐(app.state.scheduler)에 넣고 결과를 기다린다.
from fastapi import APIRouter, Request

from .models import TaxiMeterRequest, TaxiMeterResponse

router = APIRouter()


@router.post("/taxi_meter", response_model=TaxiMeterResponse)
async def taxi_meter(req: TaxiMeterRequest, request: Request) -> TaxiMeterResponse:
    return TaxiMeterResponse(**await request.app.state.scheduler.submit("taxi_meter", req.image_URL))
