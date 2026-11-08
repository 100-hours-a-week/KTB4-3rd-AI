# 대화 문맥: 대화방별 압축 요약 + 요약 이후 원문. 원문 추정 토큰이 상한을 넘으면 오래된 턴을 요약에 합쳐 하한 아래로 줄인다.
import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from ..common.llm import invoke_with_budget

log = logging.getLogger("app.Server_Monitor_Bot.chat")
SUMMARIZE = """너는 운영 챗봇의 대화 요약 담당이다. 이전 요약과 새 대화를 합쳐 한국어 요약 하나로 다시 써라.
- 남길 것: 다룬 장애 id·지식 id·문서 이름, 확인된 원인·조치, 운영자가 정한 결정과 요청, 아직 답하지 못한 질문.
- 버릴 것: 인사, 같은 말 반복, 도구 결과의 긴 수치 목록.
- {limit}자 이내. 사실(수치·시각·id)은 대화에 있는 것만 쓴다."""


def tokens(text: str, chars_per_token: float) -> int:
    return int(len(text or "") / chars_per_token) + 1


def turn(row: dict) -> dict:
    return {"role": row["role"], "text": row["text"]}


def build(chat_repo, chat_id: int, llm, budget, cfg: dict) -> dict:
    high, low = cfg.get("context_high_tokens", 8000), cfg.get("context_low_tokens", 4000)
    per = cfg.get("chars_per_token", 1.5)
    saved = chat_repo.summary_of(chat_id)
    rows = chat_repo.since(chat_id, saved["upto_id"])
    if sum(tokens(r["text"], per) for r in rows) <= high:
        return {"summary": saved["summary"], "turns": [turn(r) for r in rows]}
    keep, size = [], 0
    for r in reversed(rows):
        size += tokens(r["text"], per)
        if size > low:
            break
        keep.insert(0, r)
    fold = rows[:len(rows) - len(keep)]
    try:
        limit = int(cfg.get("summary_max_tokens", 1500) * per)
        out = invoke_with_budget(llm, [SystemMessage(content=SUMMARIZE.format(limit=limit)),
                                       HumanMessage(content=json.dumps({"이전 요약": saved["summary"], "새 대화": [turn(r) for r in fold]},
                                                                       ensure_ascii=False, default=str))], budget)
        summary = (out.text if hasattr(out, "text") else str(out))[:limit]
        chat_repo.save_summary(chat_id, summary, fold[-1]["id"])
        return {"summary": summary, "turns": [turn(r) for r in keep]}
    except Exception as exc:
        log.warning("chat summary failed: error=%s", type(exc).__name__)
        return {"summary": saved["summary"], "turns": [turn(r) for r in keep]}
