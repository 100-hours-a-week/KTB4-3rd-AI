# S4a 조정 제안 그래프: match → (정책 없음·활성이면 끝) → propose(W2) → request.
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .nodes import Propose


class ProposeState(TypedDict, total=False):
    request: dict
    budget: Any
    skip: str
    policy: dict
    current: Any
    baseline: Any
    evidence: list[dict]
    value: int
    ttl: int
    reason: str
    proposal: dict
    outcome: str


def build_propose(env):
    n = Propose(env)
    g = StateGraph(ProposeState)
    for name in ("match", "propose", "request"):
        g.add_node(name, getattr(n, name))
    g.add_edge(START, "match")
    g.add_conditional_edges("match", lambda s: "end" if s.get("skip") else "propose", {"end": END, "propose": "propose"})
    g.add_edge("propose", "request")
    g.add_edge("request", END)
    return g.compile()
