from fastapi import APIRouter

from ..features.receipt.router import router as receipt
from ..features.taxi_meter.router import router as taxi_meter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    # CI 스모크 테스트와 ECS 헬스체크용. 키나 모델 상태와 무관하게 프로세스가 떠 있으면 200이다.
    return {"status": "ok"}


router.include_router(taxi_meter)
router.include_router(receipt)
