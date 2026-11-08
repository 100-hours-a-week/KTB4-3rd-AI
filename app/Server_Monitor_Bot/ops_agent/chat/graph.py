# S2 챗봇 그래프: prepare → 훈련 장애 답장이면 안내만 | 지식 변경이면 fast_edit | 도구가 필요 없으면 answer | investigate(W1) → answer(W2) → save.
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .nodes import Chat


class ChatState(TypedDict, total=False):
    type: str
    payload: dict
    chat_id: int | None
    budget: Any
    drill_reply: bool
    history: dict
    incident: dict | None
    alert: dict | None
    evidence: list[dict]
    memory: list[dict]
    decision: dict | None
    investigation: dict
    answer: dict
    chat_answer: dict
    outbox: list[dict]
    outcome: str


def build_chat(env):
    n = Chat(env)
    g = StateGraph(ChatState)
    for name in ("prepare", "drill_notice", "fast_edit", "investigate", "answer", "save"):
        g.add_node(name, getattr(n, name))
    g.add_edge(START, "prepare")
    g.add_conditional_edges("prepare", n.next_of, {k: k for k in ("drill_notice", "fast_edit", "answer", "investigate")})
    g.add_edge("fast_edit", "save")
    g.add_edge("drill_notice", END)
    g.add_edge("investigate", "answer")
    g.add_edge("answer", "save")
    g.add_edge("save", END)
    return g.compile()
