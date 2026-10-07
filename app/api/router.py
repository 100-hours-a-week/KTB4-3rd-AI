from fastapi import APIRouter

from ..features.receipt.router import router as receipt
from ..features.taxi_meter.router import router as taxi_meter

router = APIRouter()
router.include_router(taxi_meter)
router.include_router(receipt)
