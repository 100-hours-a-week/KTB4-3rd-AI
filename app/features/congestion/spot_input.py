"""관련성 선별 결과를 스팟별 LLM 입력과 원본 대상 목록으로 만든다."""

import json
from dataclasses import dataclass

from .preprocessor import CandidateSignal
from .relevance import ClassifiedSpot


@dataclass(frozen=True)
class SpotLLMInput:
    """모델에 보낼 JSON과 출력 대응에 사용할 원본 제보 정보를 보관한다."""

    payload: str
    targets: tuple[CandidateSignal, ...]


def build_spot_input(classified: ClassifiedSpot) -> SpotLLMInput | None:
    """선택된 대상이 있는 묶음의 전체 문맥을 스팟 입력 하나로 직렬화한다.

    targets만 판정 대상이다. 문맥용 원글이나 무관한 원글·댓글은 선택된 묶음 안에서 문맥으로
    남기며, 그 자체를 판정·집계 대상으로 승격하지 않는다. 대상이 없으면 입력도 없다.
    """
    targets = classified.selected_candidates
    if not targets:
        return None

    prepared = classified.prepared
    post_ids = {target.post_id for target in targets}
    payload = {
        "spot_id": prepared.spot_id,
        "as_of": prepared.as_of.isoformat(),
        "targets": [
            {"signal_type": target.signal_type, "signal_id": target.signal_id}
            for target in targets
        ],
        "post_bundles": [
            post.model_dump(mode="json")
            for post in prepared.post_bundles
            if post.post_id in post_ids
        ],
    }
    return SpotLLMInput(
        payload=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        targets=targets,
    )
