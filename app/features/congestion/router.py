from typing import Annotated

from fastapi import APIRouter, Depends

from .models import CongestionAnalysisRequest, CongestionAnalysisResponse
from .service import CongestionAnalyzer, analyze_request, get_analyzer

router = APIRouter()


@router.post(
    "/congestion-analyses",
    response_model=CongestionAnalysisResponse,
    summary="스팟별 혼잡도 분석",
    description=(
        "Spring이 보낸 글·댓글을 스팟별로 분석해 혼잡도·작성자 수·원인·요약을 반환합니다. "
        "문서의 요청·응답은 합성 예시이며 실제 모델 결과를 보장하지 않습니다. "
        "같은 작성자의 반복 제보는 중복 집계하지 않습니다. "
        "빈 스팟도 SUCCESS이며 UNKNOWN·0명·빈 원인으로 반환합니다. "
        "스팟 분석 실패는 HTTP 200 안에서 FAILED·result=null·"
        "code=ANALYSIS_FAILED·message='analysis failed'로 반환합니다."
    ),
)
def congestion_analysis(
    req: CongestionAnalysisRequest,
    analyzer: Annotated[CongestionAnalyzer, Depends(get_analyzer)],
) -> CongestionAnalysisResponse:
    return analyze_request(req, analyzer)
