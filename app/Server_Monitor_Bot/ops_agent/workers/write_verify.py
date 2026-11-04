# W2 작성·검증: 구조화 출력 → 결정적 검증 → 오류·쓸 수 있는 근거 ID·수치를 주고 틀린 필드만 고쳐 재작성(한도는 부르는 쪽이 정함).
import json
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from ..common import validators
from ..common.evidence import haystack, known_ids, number_tokens
from ..common.llm import invoke_with_budget
from ..common.schemas import SCHEMAS


class WriteState(TypedDict, total=False):
    schema: str
    instructions: str
    inputs: dict
    evidence: list[dict]
    max_retries: int
    retries: int
    output: dict | None
    errors: list[str]
    valid: bool
    budget: Any


def render(s: WriteState) -> list:
    body = {"입력": s["inputs"], "근거": s.get("evidence", [])}
    text = json.dumps(body, ensure_ascii=False, default=str)
    if s.get("errors"):
        text += "\n\n이전 출력:\n" + json.dumps(s.get("output"), ensure_ascii=False, default=str)
        text += "\n검증 오류(모두 고쳐서 다시 써라. 오류가 난 필드만 고치고 나머지 필드는 이전 출력 그대로 둔다):\n- " + "\n- ".join(s["errors"])
        ev = s.get("evidence", [])
        text += "\n쓸 수 있는 근거 ID: " + ", ".join(sorted(known_ids(ev, s["inputs"])))
        text += "\n쓸 수 있는 수치: " + ", ".join(sorted(number_tokens(haystack(ev, s["inputs"])), key=lambda n: (len(n), n))[:200])
    return [SystemMessage(content=s["instructions"]), HumanMessage(content=text)]


def build_write_verify(llm):
    def write(s: WriteState) -> dict:
        retries = s.get("retries", 0) + (1 if s.get("errors") else 0)
        structured = llm.with_structured_output(SCHEMAS[s["schema"]], include_raw=True)
        out = invoke_with_budget(structured, render(s), s.get("budget"))
        parsed = out.get("parsed")
        if parsed is None:
            return {"output": None, "retries": retries, "errors": [f"형식 오류: {out.get('parsing_error')}"]}
        return {"output": parsed.model_dump() if hasattr(parsed, "model_dump") else dict(parsed), "retries": retries}

    def verify(s: WriteState) -> dict:
        if s.get("output") is None:
            return {"errors": s.get("errors") or ["형식 오류"]}
        return {"errors": validators.common(s["output"], s.get("evidence", []), s["inputs"])
                          + validators.BY_SCHEMA[s["schema"]](s["output"], s["inputs"])}

    def finish(s: WriteState) -> dict:
        return {"valid": not s.get("errors")}

    g = StateGraph(WriteState)
    g.add_node("write", write)
    g.add_node("verify", verify)
    g.add_node("finish", finish)
    g.add_edge(START, "write")
    g.add_edge("write", "verify")
    g.add_conditional_edges("verify", lambda s: "write" if (s.get("errors") and s.get("retries", 0) < s["max_retries"]) else "finish",
                            {"write": "write", "finish": "finish"})
    g.add_edge("finish", END)
    return g.compile()


def run_write(graph, *, schema: str, instructions: str, inputs: dict, evidence: list[dict], max_retries: int, budget) -> dict:
    out = graph.invoke({"schema": schema, "instructions": instructions, "inputs": inputs, "evidence": evidence,
                        "max_retries": max_retries, "retries": 0, "errors": [], "budget": budget}, {"recursion_limit": 4 * max_retries + 10})
    return {"output": out.get("output"), "valid": out.get("valid", False), "errors": out.get("errors", []), "retries": out.get("retries", 0),
            "first_pass": out.get("valid", False) and out.get("retries", 0) == 0}
