# 서브에이전트 공용 실행 환경: 컨텍스트, 모델, W1·W2 그래프, 판단 모델, 판정자(판단 모델 우선, 실패면 LLM), 근거 변환.
import json
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from .llm import invoke_with_budget
from .schemas import JudgeResult


@dataclass
class Env:
    ctx: Any
    llm: Any
    w1: Any
    w2: Any
    decider: Any = None

    @property
    def agent(self) -> dict:
        return self.ctx.settings.section("agent")

    @property
    def policy_keys(self) -> tuple:
        return tuple(self.ctx.settings.policy)

    def owners(self) -> tuple[int, ...]:
        return self.ctx.settings.admin_ids

    def judge(self, prompt: str, expected: str, predicted: str, budget=None) -> tuple[bool | None, str]:
        if self.decider is not None:
            hit, reason = self.decider.same_cause(expected, predicted)
            if hit is not None:
                return hit, reason
        try:
            out = invoke_with_budget(self.llm.with_structured_output(JudgeResult, include_raw=True),
                                     [SystemMessage(content=prompt),
                                      HumanMessage(content=json.dumps({"정답": expected, "예측": predicted}, ensure_ascii=False))], budget)
            parsed = out.get("parsed")
            return (parsed.hit, parsed.reason) if parsed else (None, "판정 형식 오류")
        except Exception as exc:
            return None, f"판정 실패: {type(exc).__name__}"


def as_evidence(result: dict, tool: str, args: dict | None = None) -> dict | None:
    if not isinstance(result, dict) or "error" in result or not result.get("evidence_id"):
        return None
    return {"id": result["evidence_id"], "tool": tool, "args": args or {}, "summary": result.get("summary"),
            "data": json.dumps(result.get("data"), ensure_ascii=False, default=str)[:3000]}


def hm(ts: float | None = None) -> str:
    from ...channels.telegram.render import kst

    return kst(ts)
