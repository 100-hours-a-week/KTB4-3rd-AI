# 모니터 모델 호출: ChatOpenAI(Responses API) 하나와 실행 예산(토큰·시간) 관리.
import time


class BudgetExceeded(Exception):
    pass


class Budget:
    def __init__(self, tokens: int = 30000, ms: int = 120000, reserve_tokens: int = 5000, reserve_ms: int = 25000):
        self.tokens, self.ms = tokens, ms
        self.reserve_tokens, self.reserve_ms = reserve_tokens, reserve_ms
        self.started = time.monotonic()
        self.input_tokens = self.output_tokens = self.tool_ms = 0

    @property
    def used(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)

    def exhausted(self, reserve: bool = True) -> bool:
        rt, rm = (self.reserve_tokens, self.reserve_ms) if reserve else (0, 0)
        return self.used >= self.tokens - rt or self.elapsed_ms >= self.ms - rm

    def add(self, message) -> None:
        usage = getattr(message, "usage_metadata", None) or {}
        self.input_tokens += int(usage.get("input_tokens") or 0)
        self.output_tokens += int(usage.get("output_tokens") or 0)


def make_chat(settings):
    from langchain_openai import ChatOpenAI

    model = settings.section("model")
    return ChatOpenAI(model=model.get("chat", "gpt-6-luna"), use_responses_api=True, timeout=model.get("timeout_s", 30),
                      max_retries=1, api_key=settings.openai_key or "missing-monitor-key")


def invoke_with_budget(runnable, messages, budget: Budget | None):
    if budget is not None and budget.exhausted(reserve=False):
        raise BudgetExceeded()
    out = runnable.invoke(messages)
    if budget is not None:
        budget.add(out["raw"] if isinstance(out, dict) and "raw" in out else out)
    return out
