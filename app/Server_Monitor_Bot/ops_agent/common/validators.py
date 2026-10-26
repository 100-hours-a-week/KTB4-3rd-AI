# W2 결정적 검증기: 공통(근거 ID·수치·파일:줄·비밀) + 스키마별 규칙.
import re

from ...hooks.mask import mask
from ...knowledge.schema import STEPS, validate as validate_item
from .evidence import haystack, known_ids, number_tokens
from .schemas import to_item

NUMBER = re.compile(r"(?<![A-Za-z0-9_.:/-])(\d{2,}(?:\.\d+)?)(?![0-9_%:])")
CLOCK = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b|\b\d{4}-\d{2}-\d{2}\b")
FILE_LINE = re.compile(r"\b((?:app/)?[\w./-]+\.py):(\d+)")
TEXT_FIELDS = ("summary", "cause_text", "resolution_text", "answer_text", "reason")


def texts(output: dict) -> list[str]:
    out = [str(output[k]) for k in TEXT_FIELDS if output.get(k)]
    k = output.get("knowledge")
    if isinstance(k, dict):
        out += [str(k.get(f) or "") for f in ("symptom", "cause", "resolution")] + [str(c) for c in k.get("checks") or []]
    return out


def common(output: dict, evidence: list[dict], inputs: dict) -> list[str]:
    errors = []
    hay = haystack(evidence, inputs)
    ids = known_ids(evidence, inputs)
    numbers = number_tokens(hay)
    for eid in output.get("evidence_ids") or []:
        if eid not in ids:
            errors.append(f"없는 근거 ID: {eid}")
    for text in texts(output):
        clean = CLOCK.sub(" ", text)
        for num in NUMBER.findall(clean):
            if num not in numbers:
                errors.append(f"근거·입력에 없는 수치: {num}")
        for path, line in FILE_LINE.findall(text):
            if f"{path.removeprefix('app/')}:{line}" not in hay and f"{path}:{line}" not in hay:
                errors.append(f"read_code로 확인하지 않은 파일:줄: {path}:{line}")
        if mask(text) != text:
            errors.append("비밀 값 패턴이 들어 있음")
    return errors


def diagnosis_report(output: dict, inputs: dict) -> list[str]:
    errors = []
    if output.get("step_id") not in STEPS:
        errors.append(f"모르는 step_id: {output.get('step_id')}")
    if output.get("policy_key") not in (None, *inputs.get("policy_keys", ())):
        errors.append(f"정책 표에 없는 policy_key: {output.get('policy_key')}")
    active = set(inputs.get("active_knowledge_ids") or [])
    errors += [f"없는 지식 id: {k}" for k in output.get("knowledge_ids") or [] if k not in active]
    if output.get("unknown") and output.get("confidence") != "low":
        errors.append("unknown=true면 confidence는 low")
    return errors


def chat_answer(output: dict, inputs: dict) -> list[str]:
    if output.get("intent") in ("knowledge_update", "knowledge_delete"):
        if output.get("target_knowledge_id") not in set(inputs.get("active_knowledge_ids") or []):
            return [f"수정·삭제 대상 지식이 없거나 삭제됨: {output.get('target_knowledge_id')}"]
    if str(output.get("intent", "")).startswith("knowledge_") and not output.get("edit_request"):
        return ["지식 변경 의도면 edit_request 필요"]
    return []


def knowledge_draft(output: dict, inputs: dict) -> list[str]:
    errors = []
    op = output.get("op")
    if inputs.get("op") and op != inputs["op"]:
        errors.append(f"요청한 작업({inputs['op']})과 op({op})가 다름")
    if op in ("update", "delete") and output.get("target_id") not in set(inputs.get("active_knowledge_ids") or []):
        errors.append(f"대상 지식이 없거나 삭제됨: {output.get('target_id')}")
    if op != "delete":
        item = to_item(output.get("knowledge") or {})
        errors += validate_item({**item, "id": "KN_CHECK"}, inputs.get("policy_keys", ()))
    return errors


def adjust_proposal(output: dict, inputs: dict) -> list[str]:
    p = inputs.get("policy") or {}
    errors = []
    if not (p.get("min", 0) <= output.get("value", -1) <= p.get("max", 0)):
        errors.append(f"정책 범위 밖 값: {output.get('value')} (허용 {p.get('min')}~{p.get('max')})")
    if not (1 <= output.get("ttl_min", 0) <= inputs.get("max_ttl_min", 0)):
        errors.append(f"유지 시간 범위 밖: {output.get('ttl_min')} (최대 {inputs.get('max_ttl_min')})")
    if inputs.get("stage") == "prod" and inputs.get("baseline") and output.get("value", 0) < inputs["baseline"] * 0.5:
        errors.append("prod는 이전 값의 50% 이상 유지")
    return errors


BY_SCHEMA = {"diagnosis_report": diagnosis_report, "chat_answer": chat_answer, "knowledge_draft": knowledge_draft,
             "adjust_proposal": adjust_proposal, "judge": lambda output, inputs: []}
