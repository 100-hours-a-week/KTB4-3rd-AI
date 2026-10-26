# W1 조사: 받은 도구 묶음으로 tool calling 루프를 돌려 근거를 모은다(도구 라운드 한도·예산).
import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextvars import ContextVar, copy_context
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from ...hooks.mask import mask
from ..common import evidence
from ..common.llm import BudgetExceeded, invoke_with_budget
from ..tools.registry import hide_knowledge


TOOL_TIMEOUT = 10.0
_cache: ContextVar[dict | None] = ContextVar("w1_tool_cache", default=None)


def guard(tool_, timeout: float = TOOL_TIMEOUT) -> StructuredTool:
    def run(**kwargs):
        cache = _cache.get()
        key = (tool_.name, json.dumps(kwargs, sort_keys=True, default=str))
        if cache is not None and key in cache:
            return cache[key]
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(copy_context().run, tool_.func, **kwargs)
        try:
            result = future.result(timeout=timeout)
        except FutureTimeout:
            result = {"error": f"{tool_.name} 시간 초과({timeout:g}초)"}
        except Exception as exc:
            result = {"error": f"{tool_.name} 실패: {type(exc).__name__}"}
        finally:
            pool.shutdown(wait=False)
        if cache is not None:
            cache[key] = result
        return result
    return StructuredTool.from_function(func=run, name=tool_.name, description=tool_.description, args_schema=tool_.args_schema)


class InvestigateState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    tool_set: list[str]
    max_rounds: int
    rounds: int
    budget: Any
    stop_reason: str
    tool_started: float
    evidence: list[dict]


def build_investigate(llm, tools_registry: dict, timeout: float = TOOL_TIMEOUT):
    tools_registry = {name: guard(t, timeout) for name, t in tools_registry.items()}
    tool_node = ToolNode(list(tools_registry.values()))

    def call_model(s: InvestigateState) -> dict:
        tools = [tools_registry[n] for n in s["tool_set"] if n in tools_registry]
        last_round = s["rounds"] >= s["max_rounds"]
        model = llm.bind_tools(tools, tool_choice="none" if last_round else "auto")
        try:
            msg = invoke_with_budget(model, s["messages"], s["budget"])
        except BudgetExceeded:
            return {"stop_reason": "budget", "messages": [AIMessage(content="[예산 초과로 조사 중단]")]}
        except Exception as exc:
            return {"stop_reason": "error", "messages": [AIMessage(content=f"[모델 호출 실패: {type(exc).__name__}]")]}
        if last_round:
            msg = AIMessage(content=msg.content, id=msg.id, usage_metadata=getattr(msg, "usage_metadata", None))
        return {"messages": [msg]}

    def after_model(s: InvestigateState) -> str:
        last = s["messages"][-1]
        if s.get("stop_reason") or not getattr(last, "tool_calls", None):
            return "collect"
        if s["rounds"] >= s["max_rounds"] or (s["budget"] is not None and s["budget"].exhausted()):
            return "collect"
        calls = last.tool_calls
        return "blocked" if any(c["name"] not in set(s["tool_set"]) for c in calls) else "tools"

    def blocked(s: InvestigateState) -> dict:
        allowed = set(s["tool_set"])
        return {"messages": [evidence.blocked_message(c, allowed) for c in s["messages"][-1].tool_calls], "rounds": s["rounds"] + 1}

    def mark(s: InvestigateState) -> dict:
        return {"tool_started": time.monotonic()}

    def after_tools(s: InvestigateState) -> dict:
        if s["budget"] is not None:
            s["budget"].tool_ms += int((time.monotonic() - s.get("tool_started", time.monotonic())) * 1000)
        caller = next(m for m in reversed(s["messages"]) if isinstance(m, AIMessage))
        calls = {c["id"] for c in caller.tool_calls}
        fixed = [ToolMessage(content=evidence.cut(mask(m.content if isinstance(m.content, str) else evidence.dumps(m.content))),
                             tool_call_id=m.tool_call_id, name=m.name, id=m.id)
                 for m in s["messages"] if isinstance(m, ToolMessage) and m.tool_call_id in calls]
        return {"messages": fixed, "rounds": s["rounds"] + 1}

    def collect(s: InvestigateState) -> dict:
        reason = s.get("stop_reason") or ("max_rounds" if s["rounds"] >= s["max_rounds"] else "enough")
        if s["budget"] is not None and s["budget"].exhausted() and reason == "enough":
            reason = "budget"
        return {"evidence": evidence.collect(s["messages"]), "stop_reason": reason}

    g = StateGraph(InvestigateState)
    g.add_node("model", call_model)
    g.add_node("mark", mark)
    g.add_node("tools", tool_node)
    g.add_node("after_tools", after_tools)
    g.add_node("blocked", blocked)
    g.add_node("collect", collect)
    g.add_edge(START, "model")
    g.add_conditional_edges("model", after_model, {"tools": "mark", "blocked": "blocked", "collect": "collect"})
    g.add_edge("mark", "tools")
    g.add_edge("tools", "after_tools")
    g.add_edge("after_tools", "model")
    g.add_edge("blocked", "model")
    g.add_edge("collect", END)
    return g.compile()


def run_investigate(graph, *, system: str, goal: str, context: str, tool_set: list[str], max_rounds: int, budget,
                    hide: list[str] = ()) -> dict:
    token, cache = hide_knowledge.set(tuple(hide)), _cache.set({})
    try:
        out = graph.invoke({"messages": [SystemMessage(content=system), HumanMessage(content=f"목표: {goal}\n\n맥락:\n{context}")],
                            "tool_set": tool_set, "max_rounds": max_rounds, "rounds": 0, "budget": budget},
                           {"recursion_limit": 6 * max_rounds + 10})
    finally:
        hide_knowledge.reset(token)
        _cache.reset(cache)
    final = next((m.content for m in reversed(out["messages"]) if isinstance(m, AIMessage) and isinstance(m.content, str)), "")
    return {"evidence": out.get("evidence", []), "rounds": out.get("rounds", 0), "stop_reason": out.get("stop_reason", "enough"),
            "notes": final}
