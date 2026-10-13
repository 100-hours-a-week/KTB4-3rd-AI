from typing import Annotated

from fastapi import APIRouter, Body, Depends

from .models import CongestionAnalysisRequest, CongestionAnalysisResponse
from .schema_examples import REQUEST_EXAMPLE
from .service import CongestionAnalyzer, analyze_request, get_analyzer

router = APIRouter()


@router.post("/congestion-analyses", response_model=CongestionAnalysisResponse)
def congestion_analysis(
    # 모델 스키마의 알파벳 정렬을 피하고 요청 예시의 필드 작성 순서를 유지한다.
    req: Annotated[
        CongestionAnalysisRequest,
        Body(openapi_examples={"default": {"value": REQUEST_EXAMPLE}}),
    ],
    analyzer: Annotated[CongestionAnalyzer, Depends(get_analyzer)],
) -> CongestionAnalysisResponse:
    return analyze_request(req, analyzer)
