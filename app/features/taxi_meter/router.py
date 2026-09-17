from fastapi import APIRouter

from .main import run
from .models import TaxiMeterRequest, TaxiMeterResponse

router = APIRouter()


@router.post("/taxi_meter", response_model=TaxiMeterResponse)
def taxi_meter(req: TaxiMeterRequest) -> TaxiMeterResponse:
    return TaxiMeterResponse(**run(req.image_URL))
