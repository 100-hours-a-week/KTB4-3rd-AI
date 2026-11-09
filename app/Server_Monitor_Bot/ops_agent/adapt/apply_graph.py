# S4b 조정 적용·검증 그래프: approved → apply / observe → conclude → (악화면 rollback) → report.
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .nodes import Apply


class ApplyState(TypedDict, total=False):
    type: str
    payload: dict
    chat_id: int | None
    skip: bool
    adj: dict
    before: dict
    after: dict
    verdict: str
    summary_line: str | None
    followups: list[dict]
    outbox: list[dict]
    outcome: str


def build_apply(env, retry_waits=(1, 2, 4)):
    n = Apply(env, retry_waits)
    g = StateGraph(ApplyState)
    for name in ("apply", "observe", "conclude", "rollback", "report"):
        g.add_node(name, getattr(n, name))
    g.add_conditional_edges(START, lambda s: "apply" if s["type"] == "adapt.approved" else "observe",
                            {"apply": "apply", "observe": "observe"})
    g.add_edge("apply", END)
    g.add_conditional_edges("observe", lambda s: "end" if s.get("skip") else "conclude", {"end": END, "conclude": "conclude"})
    g.add_conditional_edges("conclude", lambda s: "rollback" if s["verdict"] == "worse" else "report",
                            {"rollback": "rollback", "report": "report"})
    g.add_edge("rollback", "report")
    g.add_edge("report", END)
    return g.compile()
