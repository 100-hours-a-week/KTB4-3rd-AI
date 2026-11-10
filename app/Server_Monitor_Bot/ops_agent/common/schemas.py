# 구조화 출력 스키마: 진단 리포트, 챗봇 답, 지식·문서 정리본, 조정 제안, 판정자 결과.
from typing import Literal

from pydantic import BaseModel, Field


class ExpectedIssue(BaseModel):
    problem: str = Field(description="이어서 생길 수 있는 문제 한 줄")
    solution: str = Field(description="그 문제의 해결·예방 조치 한 줄")
    evidence_ids: list[str] = Field(description="이 예상의 근거 ID(지식 kn:, 문서 doc:, 도구 결과). 1개 이상")


class DiagnosisReport(BaseModel):
    summary: str = Field(description="한 줄 요약. 예: 영수증 luna_parse 실패 6/11 (5분)")
    cause_text: str = Field(description="원인 서술 300자 이내. 근거로 정하지 못하면 '원인 미확정'과 남은 후보")
    resolution_text: str = Field(description="해결 제안 300자 이내")
    step_id: str = Field(description="실패 단계 id")
    confidence: Literal["high", "medium", "low"]
    evidence_ids: list[str] = Field(description="인용한 근거 ID")
    knowledge_ids: list[str] = Field(description="참고한 지식 id")
    policy_key: str | None = Field(description="자동 조정 가능하면 정책 표 키, 아니면 null")
    unknown: bool = Field(description="근거로 원인을 정하지 못했으면 true")
    expected_issues: list[ExpectedIssue] = Field(description="근거가 있는 예상 문제와 해결 0~3개. 근거가 없으면 빈 목록")


class ChatAnswer(BaseModel):
    answer_text: str = Field(description="답 400자 이내. 결론 먼저")
    evidence_ids: list[str]
    intent: Literal["answer", "knowledge_add", "knowledge_update", "knowledge_delete", "doc_update", "doc_delete", "out_of_scope"]
    target_knowledge_id: str | None = Field(description="수정·삭제할 지식 id, 아니면 null")
    target_doc: str | None = Field(description="고치거나 지울 문서 절 근거 ID(doc:<문서>#<번호>), 아니면 null")
    incident_id: str | None = Field(description="이 대화가 다루는 장애 id, 아니면 null")
    edit_request: str | None = Field(description="지식·문서 변경 요청을 사용자 말로 요약, 아니면 null")
    expected_issues: list[ExpectedIssue] = Field(description="근거가 있는 예상 문제와 해결 0~3개. 근거가 없으면 빈 목록")


class Match(BaseModel):
    step_id: list[str] | None = None
    status: list[int] | None = None
    error_code: list[str] | None = None
    reason: list[str] | None = None
    head: list[str] | None = None
    min_ms: int | None = None
    min_ratio: float | None = None


class KnowledgeItem(BaseModel):
    title: str
    features: list[str]
    step_ids: list[str]
    match: Match
    symptom: str
    cause: str
    resolution: str
    checks: list[str]
    policy_key: str | None
    alert: Literal["immediate", "diagnose"]
    severity: Literal["high", "medium", "low"]


class KnowledgeDraft(BaseModel):
    op: Literal["add", "update", "delete"]
    target_id: str | None
    knowledge: KnowledgeItem
    reason: str
    source_refs: list[str]
    duplicate_of: str | None


class DocDraft(BaseModel):
    op: Literal["update", "delete"]
    text: str = Field(description="update면 고친 절 본문 전체(Markdown), delete면 빈 문자열")
    reason: str = Field(description="고치거나 지우는 이유 한두 문장")
    source_refs: list[str] = Field(description="근거가 된 chat:<message_id>, doc:<문서>#<번호>")


class AdjustProposal(BaseModel):
    value: int = Field(description="새 값(정책 표 범위 안). route.force_luna는 1")
    ttl_min: int = Field(description="유지 시간(분)")
    reason: str = Field(description="근거 한두 줄(현재 지표 인용)")
    evidence_ids: list[str]


class JudgeResult(BaseModel):
    hit: bool
    reason: str


SCHEMAS = {"diagnosis_report": DiagnosisReport, "chat_answer": ChatAnswer, "knowledge_draft": KnowledgeDraft, "doc_draft": DocDraft,
           "adjust_proposal": AdjustProposal, "judge": JudgeResult}


def to_item(item: KnowledgeItem | dict) -> dict:
    d = item.model_dump() if isinstance(item, BaseModel) else dict(item)
    match = d.get("match") or {}
    d["match"] = {k: v for k, v in (match.items() if isinstance(match, dict) else match) if v not in (None, [])}
    return d
