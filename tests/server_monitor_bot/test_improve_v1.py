# 개선v1: 디스코드 팀원, 대기열(우선순위·20건·중복·접수 안내), 판단 모델, 챗봇 경로, 지난 대화 근거 금지, 렌더 정리, 도구 묶음, 훈련 표식 가리기.
import json
import time
from types import SimpleNamespace

from app.Server_Monitor_Bot.channels.telegram import render
from app.Server_Monitor_Bot.config.settings import load
from app.Server_Monitor_Bot.ops_agent.chat.nodes import Chat, memory_of
from app.Server_Monitor_Bot.ops_agent.common.decide import Decider
from app.Server_Monitor_Bot.ops_agent.common.validators import chat_answer
from app.Server_Monitor_Bot.ops_agent.roles import role_of
from app.Server_Monitor_Bot.ops_agent.tools import raw, registry
from app.Server_Monitor_Bot.runtime.context import new_event
from app.Server_Monitor_Bot.runtime.scheduler import Scheduler

DISCORD = 1542523292685045999


def settings():
    return load({"AI_DB_URL": "mysql://u:p@h/ai_local", "Admin_Telegram_ID": "1", "DISCORD_BOT_TOKEN": "x", "DISCORD_CHANNEL_ID": str(DISCORD)})


def test_discord_channel_users_are_members():
    s, members = settings(), SimpleNamespace(is_member=lambda uid: False)
    assert role_of(7, s, members, DISCORD) == "member"
    assert role_of(7, s, members, 99) is None
    assert role_of(1, s, members, DISCORD) == "admin"


class Sender:
    def __init__(self):
        self.sent = []

    def send(self, chat, text, buttons=None, reply_to=None, html=False):
        self.sent.append((chat, text))
        return len(self.sent)


def user(text, uid=5):
    return new_event("user.message", {"text": text, "message_id": 1}, source="telegram", from_id=uid, chat_id=9)


def test_queue_limit_dedupe_ack_and_priority():
    out = Sender()
    q = Scheduler(None, "t", out, user_max=2, dedupe_s=60)
    assert q.enqueue(user("a")) and q.enqueue(user("b"))
    assert out.sent == [(9, "🔎 접수 · 대기 1번째"), (9, "🔎 접수 · 대기 2번째")]
    assert not q.enqueue(user("c")) and "가득" in out.sent[-1][1]
    assert q.enqueue(new_event("alert.pipeline"))
    assert q.get()["type"] == "alert.pipeline"
    first = q.get()
    assert first["payload"]["text"] == "a" and first["ack"] == (9, 1)
    assert not q.enqueue(user("b")) and out.sent[-1][1] == "같은 질문을 처리 중입니다"
    assert q.enqueue(user("b", uid=6))


class Answers:
    def __init__(self, answers):
        self.answers = answers

    def create(self, **kw):
        self.kw = kw
        return SimpleNamespace(answers=[SimpleNamespace(name=k, **v) for k, v in self.answers.items()])


def decider(answers):
    return Decider(settings(), client=SimpleNamespace(decisions=Answers(answers)))


def test_decider_route_threshold_and_same_cause():
    d = decider({"intent": {"choice": "knowledge_add", "confidence": 0.9}, "kind": {"choice": "status", "confidence": 0.4},
                 "needs_tools": {"probability": 0.2}})
    r = d.route("이거 지식으로 저장해")
    assert r["intent"] == "knowledge_add" and r["kind"] is None and r["needs_tools"] == 0.2
    assert decider({"same": {"probability": 0.8}}).same_cause("한도 초과", "429 한도")[0] is True
    assert Decider(SimpleNamespace(section=lambda n: {}, openai_key=None)).route("x") is None


def chat_with(active=("KN_A",)):
    env = SimpleNamespace(ctx=SimpleNamespace(repos=SimpleNamespace(knowledge=SimpleNamespace(titles=lambda limit: [{"id": k} for k in active]))))
    return Chat(env)


def state(decision, text="질문", type_="user.message"):
    return {"decision": decision, "payload": {"text": text}, "type": type_, "incident": None, "evidence": []}


def test_chat_routes_by_decision():
    c = chat_with()
    assert c.next_of(state({"intent": "knowledge_add"})) == "fast_edit"
    assert c.next_of(state({"intent": "knowledge_update"}, "KN_A 고쳐")) == "fast_edit"
    assert c.next_of(state({"intent": "knowledge_update", "kind": "incident", "needs_tools": 0.9}, "그 지식 고쳐")) == "investigate"
    assert c.next_of(state({"intent": "answer", "kind": "status", "needs_tools": 0.2})) == "answer"
    assert c.next_of(state({"intent": "answer", "kind": "trace", "needs_tools": 0.2})) == "investigate"
    assert c.next_of(state({"intent": "answer", "kind": "trace", "needs_tools": 0.2}, type_="user.trace")) == "answer"
    assert c.next_of(state(None)) == "investigate"
    a = c.fast_edit(state({"intent": "knowledge_delete"}, "KN_A 지워"))["answer"]
    assert a["intent"] == "knowledge_delete" and a["target_knowledge_id"] == "KN_A" and a["edit_request"] == "KN_A 지워"


def test_memory_is_labeled_and_never_evidence():
    rows = [{"chat_id": DISCORD, "question": "q", "answer": "a", "ts": time.time()}, {"chat_id": 9, "question": "q2", "answer": "a2", "ts": time.time()}]
    m = memory_of(rows, DISCORD)
    assert m[0]["출처"].startswith("디스코드 · ") and m[1]["출처"].startswith("텔레그램 · ") and "이전 답(사실 아님)" in m[0]
    assert chat_answer({"evidence_ids": ["chat:12"], "intent": "answer"}, {})
    text, html = render.chat_answer("결론", [], [], m)
    assert html and "지난 대화 · 디스코드" in text


def test_render_cleanups():
    assert "<code>a.py</code>" in render.chat_answer("`a.py` 확인", [], [])[0]
    assert render.policy_signal("receipt.luna_call@1", "p").startswith("정책 신호 · 영수증 · OpenAI 호출(luna_call)")
    assert render.policy_signal("llm.max_concurrency", "p").startswith("정책 신호 · LLM 동시 호출 상한")
    zero = {"id": "metric:receipt.luna_call@10:00", "summary": "", "data": json.dumps({"minutes": 5, "now": {"total": 0}, "before": {"total": 0}})}
    assert render.summary_lines([zero], []) == []
    assert render.with_issues("답", None) == "답"


def test_bundles_skip_chat_and_cloudwatch_without_aws(monkeypatch):
    monkeypatch.setattr(registry, "aws", lambda: False)
    names = registry.bundle("S2")
    assert "search_chat" not in names and "query_cloudwatch" not in names and "check_overview" in names
    monkeypatch.setattr(registry, "aws", lambda: True)
    assert "query_cloudwatch" in registry.bundle("S2")


def test_trace_hides_drill_marker_in_drill_mode():
    rec = {"result": {"feature": "receipt", "result": "fail", "code": "X", "step_id": "image_fetch", "reason": "http_4xx", "latency_ms": 5,
                      "drill": True},
           "logs": [{"head": "fetch", "message": "HTTPStatusError drill 403"}], "llm_calls": []}
    registry.bind(SimpleNamespace(repos=SimpleNamespace(records=SimpleNamespace(request_trace=lambda rid: rec))))
    token = registry.drill_mode.set(True)
    try:
        out = raw.trace("r1")
    finally:
        registry.drill_mode.reset(token)
    assert "drill" not in out["data"]["result"] and "drill" not in json.dumps(out["data"]["logs"]).lower()
    assert "drill" in raw.trace("r1")["data"]["result"]


def test_empty_buffer_dir_uses_default():
    assert str(load({"SERVER_BOT_DIR": ""}).buffer_dir) == "/tmp/server_bot"
