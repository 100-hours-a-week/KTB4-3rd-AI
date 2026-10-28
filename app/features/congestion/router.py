from typing import Annotated

from fastapi import APIRouter, Body, Depends

from .models import CongestionAnalysisMessage, CongestionAnalysisResult
from .schema_examples import REQUEST_EXAMPLE
from .service import CongestionAnalyzer, analyze_message, get_analyzer

router = APIRouter()


@router.post("/congestion-analyses", response_model=CongestionAnalysisResult)
def congestion_analysis(
    # 모델 스키마의 알파벳 정렬을 피하고 요청 예시의 필드 작성 순서를 유지한다.
    req: Annotated[
        CongestionAnalysisMessage,
        Body(openapi_examples={"default": {"value": REQUEST_EXAMPLE}}),
    ],
    analyzer: Annotated[CongestionAnalyzer, Depends(get_analyzer)],
) -> CongestionAnalysisResult:
    return analyze_message(req, analyzer)
