# S1 진단 그래프: context → (investigate) → report → persist. 저장소 불가면 바로 끝.
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ...channels.telegram import render
from ..state import message
from .nodes import Diagnose


class DiagState(TypedDict, total=False):
    run_id: str
    payload: dict
    drill: dict | None
    budget: Any
    store_ok: bool
    evidence: list[dict]
    knowledge_hits: list[dict]
    hide: list[str]
    investigation: dict
    report: dict
    first_pass: bool
    diagnosis_report: dict
    outbox: list[dict]
    outcome: str


def build_diagnose(env):
    n = Diagnose(env)

    def unavailable(s):
        return {"outbox": [message(render.store_unavailable(s["payload"].get("summary") or s["payload"].get("alert_key")))],
                "outcome": "error"}

    g = StateGraph(DiagState)
    for name in ("context", "investigate", "report", "persist"):
        g.add_node(name, getattr(n, name))
    g.add_node("unavailable", unavailable)
    g.add_edge(START, "context")
    g.add_conditional_edges("context", lambda s: "unavailable" if not s.get("store_ok") else
                            ("report" if (s.get("drill") or {}).get("config") in ("single", "rag") else "investigate"),
                            {"unavailable": "unavailable", "report": "report", "investigate": "investigate"})
    g.add_edge("investigate", "report")
    g.add_edge("report", "persist")
    g.add_edge("persist", END)
    g.add_edge("unavailable", END)
    return g.compile()
