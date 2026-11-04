# W2 결정적 검증기: 공통(근거 ID·수치·파일:줄·비밀) + 스키마별 규칙.
import re

from ...hooks.mask import mask
from ...knowledge.schema import STEPS, validate as validate_item
from .evidence import haystack, known_ids, number_tokens
from .schemas import to_item

NUMBER = re.compile(r"(?<![A-Za-z0-9_.:/-])(\d{2,}(?:\.\d+)?)(?![0-9_%:])")
CLOCK = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b|\b\d{4}-\d{2}-\d{2}\b")
FILE_LINE = re.compile(r"\b((?:app/)?[\w./-]+\.py):(\d+)")
TEXT_FIELDS = ("summary", "cause_text", "resolution_text", "answer_text", "reason", "text")


def texts(output: dict) -> list[str]:
    out = [str(output[k]) for k in TEXT_FIELDS if output.get(k)]
    out += [str(i.get(f) or "") for i in output.get("expected_issues") or [] for f in ("problem", "solution")]
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
    for issue in output.get("expected_issues") or []:
        cited = issue.get("evidence_ids") or []
        if not cited:
            errors.append(f"근거 없는 예상 문제: {issue.get('problem')} (근거가 없으면 빼고 '모릅니다'로 쓴다)")
        errors += [f"예상 문제의 없는 근거 ID: {eid}" for eid in cited if eid not in ids]
    if len(output.get("expected_issues") or []) > 3:
        errors.append("예상 문제는 3개까지")
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
    cited = [e for e in output.get("evidence_ids") or [] if str(e).startswith("chat:")]
    if cited:
        return [f"지난 대화는 근거가 아님: {', '.join(cited)} (evidence_ids에서 빼기)"]
    if str(output.get("intent", "")).startswith("doc_"):
        if not str(output.get("target_doc") or "").startswith("doc:") or not output.get("edit_request"):
            return ["문서 정정 의도면 target_doc(doc:<문서>#<번호>)과 edit_request 필요"]
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
        errors += [f"match.error_code에 앱 에러 코드: {c} (OpenAI 오류 코드만, 없으면 빼기)"
                   for c in (item.get("match") or {}).get("error_code") or [] if str(c).isupper()]
    return errors


def doc_draft(output: dict, inputs: dict) -> list[str]:
    if inputs.get("op") and output.get("op") != inputs["op"]:
        return [f"요청한 작업({inputs['op']})과 op({output.get('op')})가 다름"]
    if output.get("op") == "update" and not str(output.get("text") or "").strip():
        return ["고친 본문이 비었음"]
    return []


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


BY_SCHEMA = {"diagnosis_report": diagnosis_report, "chat_answer": chat_answer, "knowledge_draft": knowledge_draft, "doc_draft": doc_draft,
             "adjust_proposal": adjust_proposal, "judge": lambda output, inputs: []}
