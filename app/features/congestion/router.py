from typing import Annotated

from fastapi import APIRouter, Depends

from .models import CongestionAnalysisRequest, CongestionAnalysisResponse
from .service import CongestionAnalyzer, analyze_request, get_analyzer

router = APIRouter()


@router.post("/congestion-analyses", response_model=CongestionAnalysisResponse)
def congestion_analysis(
    req: CongestionAnalysisRequest,
    analyzer: Annotated[CongestionAnalyzer, Depends(get_analyzer)],
) -> CongestionAnalysisResponse:
    return analyze_request(req, analyzer)
