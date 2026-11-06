# llm() 호출 1회당 기록. openai.py가 last_call에 응답 정보를 남기면 router가 record_llm_call로 큐에 넣는다.
import hashlib
import queue
import time
from contextvars import ContextVar

from . import state

last_call: ContextVar[dict | None] = ContextVar("server_bot_last_call", default=None)


def prompt_ver(prompt) -> str | None:
    if not isinstance(prompt, str):
        return None
    return hashlib.sha256(prompt.encode()).hexdigest()[:8]


def record_llm_call(*, request_id: str | None, name: str, device: str, model: str | None, prompt, ms: int,
                    result: str, reason: str | None = None) -> None:
    if not state.INSTALLED:
        return
    info = last_call.get() or {}
    row = {"ts": time.time(), "request_id": request_id, "name": name, "device": device, "model": info.get("model", model),
           "prompt_ver": prompt_ver(prompt), "ms": ms, "result": result, "reason": info.get("reason") or reason,
           "status": info.get("status"), "error_code": info.get("error_code"), "input_tokens": info.get("input_tokens"),
           "output_tokens": info.get("output_tokens"), "attempts": info.get("attempts"), "openai_req": info.get("openai_req")}
    try:
        state.QUEUE.put_nowait(("llm_call", row))
    except queue.Full:
        state.STATS["dropped"] += 1
