# S3 지식 정리 그래프: collect → draft(W2) → confirm.
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .nodes import KnowledgeEdit


class KnowledgeState(TypedDict, total=False):
    edit: dict
    budget: Any
    history: list[dict]
    incident: dict | None
    target: dict | None
    similar: list
    evidence: list[dict]
    result: dict
    op: str
    draft_id: str
    outbox: list[dict]
    outcome: str


def build_knowledge(env):
    n = KnowledgeEdit(env)
    g = StateGraph(KnowledgeState)
    for name in ("collect", "draft", "confirm"):
        g.add_node(name, getattr(n, name))
    g.add_edge(START, "collect")
    g.add_edge("collect", "draft")
    g.add_edge("draft", "confirm")
    g.add_edge("confirm", END)
    return g.compile()
