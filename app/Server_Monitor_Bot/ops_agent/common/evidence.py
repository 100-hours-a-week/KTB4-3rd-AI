# 근거 ID: 도구 결과 감싸기, ToolMessage에서 근거 목록 모으기, 묶음 밖 호출 오류 메시지.
import json
import re

from langchain_core.messages import AIMessage, ToolMessage

LIMIT = 3000


def wrap(evidence_id: str, summary: str, data) -> dict:
    return {"evidence_id": evidence_id, "summary": summary, "data": data}


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def cut(text: str, limit: int = LIMIT) -> str:
    return text if len(text) <= limit else text[:limit] + "…(잘림)"


def parse(content: str) -> dict:
    try:
        value = json.loads(content)
        return value if isinstance(value, dict) else {"data": value}
    except (TypeError, ValueError):
        m = re.search(r'"evidence_id":\s*"([^"]+)"', content or "")
        return {"evidence_id": m.group(1) if m else None, "data": content}


def collect(messages: list) -> list[dict]:
    args = {c["id"]: c for m in messages if isinstance(m, AIMessage) for c in (m.tool_calls or [])}
    out = []
    for m in messages:
        if isinstance(m, ToolMessage):
            body = parse(m.content if isinstance(m.content, str) else dumps(m.content))
            call = args.get(m.tool_call_id, {})
            out.append({"id": body.get("evidence_id"), "tool": m.name or call.get("name"), "args": call.get("args", {}),
                        "summary": body.get("summary") or body.get("error"), "data": cut(dumps(body.get("data", body)))})
    return out


def blocked_message(call: dict, allowed) -> ToolMessage:
    if call["name"] in allowed:
        text = "같은 턴에 허용되지 않은 도구 호출이 섞여 실행하지 않았습니다. 허용 도구만 다시 호출하세요"
    else:
        text = f"허용되지 않은 도구입니다: {call['name']}. 허용 도구: {', '.join(sorted(allowed))}"
    return ToolMessage(content=dumps({"error": text}), tool_call_id=call["id"], name=call["name"])


def haystack(evidence: list[dict], inputs) -> str:
    return dumps(evidence) + "\n" + dumps(inputs)


def known_ids(evidence: list[dict], inputs: dict) -> set[str]:
    ids = {part.strip() for e in evidence if e.get("id") for part in str(e["id"]).split(",") if part.strip()}
    ids |= set(inputs.get("known_ids") or [])
    return ids


def number_tokens(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text))
