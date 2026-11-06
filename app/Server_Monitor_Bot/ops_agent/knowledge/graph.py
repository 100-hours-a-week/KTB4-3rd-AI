# S3 지식·문서 정리 그래프: collect → draft(W2) → confirm. 대상이 없거나 저장소 문서면 stop 안내만.
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .nodes import KnowledgeEdit


class KnowledgeState(TypedDict, total=False):
    edit: dict
    budget: Any
    history: dict
    incident: dict | None
    target: dict | None
    similar: list
    doc: dict
    stop: str
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
