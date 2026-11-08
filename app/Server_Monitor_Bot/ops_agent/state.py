# 메인 그래프 상태(MainState)와 발행 메시지 모양.
from typing import Any, TypedDict


class MainState(TypedDict, total=False):
    run_id: str
    event: dict
    source: str
    from_id: int | None
    chat_id: int | None
    role: str | None
    drop: bool
    drop_reason: str | None
    route: str
    sub_result: dict
    diagnosis_report: dict | None
    chat_answer: dict | None
    adjust_request: dict | None
    edit_request: dict | None
    followups: list[dict]
    outbox: list[dict]
    budget_tokens: int
    budget_ms: int
    tokens_used: int
    started_at: float
    drill_config: dict | None
    run_no: int
    chain_depth: int
    budget: Any
    node_path: list[str]


def message(text: str, *, chat_id: int | None = None, buttons=None, reply_to: int | None = None, ref: tuple | None = None,
            chat_row: int | None = None, edit_message_id: int | None = None, edit_text: str | None = None,
            edits: list | None = None, html: bool = False, mark: bool = False) -> dict:
    return {"text": text, "chat_id": chat_id, "buttons": buttons, "reply_to": reply_to, "ref": ref, "chat_row": chat_row,
            "edit_message_id": edit_message_id, "edit_text": edit_text, "edits": edits, "html": html, "mark": mark}


def marks(coord, kind: str, ref_id: str, buttons, text: str | None = None, hours: int | None = None) -> list[dict]:
    return [message(text or "", chat_id=chat, buttons=buttons, edit_message_id=mid, edit_text=text, mark=True)
            for chat, mid in coord.messages_of(kind, ref_id, hours)]
