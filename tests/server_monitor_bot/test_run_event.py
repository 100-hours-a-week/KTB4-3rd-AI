# run_event 배선: 가짜 저장소·텔레그램·모델로 진단→조정 제안, 대화→지식 정리, 버튼 처리 흐름을 끝까지 돌린다.
from types import SimpleNamespace

from app.Server_Monitor_Bot.config.settings import load
from app.Server_Monitor_Bot.ops_agent.main_graph import run_event
from app.Server_Monitor_Bot.runtime.context import Ctx, new_event

from .fakes import FakeChat


class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*a, **k):
            self.calls.append((name, a, k))
            return self.returns.get(name) if hasattr(self, "returns") else None
        return call


def fake_repos():
    stats = {"feature": "receipt", "step_id": "luna_call", "minutes": 5, "now": {"total": 9, "fails": 4, "rate": 0.4444,
             "reasons": [{"reason": "http_4xx", "status": 429, "n": 4}]}, "before": {"total": 9, "fails": 0, "rate": 0.0, "reasons": []}}
    k = {"id": "KN_OPENAI_RATE_LIMIT", "version": 1, "title": "한도 초과", "cause": "분당 요청 한도 초과", "resolution": "동시성 축소",
         "checks": [], "policy_key": "llm.max_concurrency", "score": 0.9, "features": [], "step_ids": ["luna_call"],
         "match": {"step_id": ["luna_call"], "status": [429]}, "symptom": "s", "alert": "immediate", "severity": "high", "status": "active"}
    coord = Recorder()
    coord.returns = {"seen_event": False, "tokens_today": 0}
    chat = Recorder()
    chat.returns = {"recent": [], "search": [], "save_turn": 11, "incident_of_message": None, "summary_of": {"summary": "", "upto_id": 0},
                    "since": []}
    docs = Recorder()
    docs.returns = {"search": []}
    adjust = Recorder()
    adjust.returns = {"flags": [], "active_for_knob": None, "propose": "A-20261026-ab12"}
    knowledge = Recorder()
    knowledge.returns = {"search": [k], "titles": [{"id": k["id"], "title": k["title"], "version": 1, "source": "seed"}], "active": [k],
                         "save_draft": "D-20261026-cd34", "apply_draft": {"ok": True, "message": "저장했습니다: x"}}
    incidents = Recorder()
    incidents.returns = {"create": "I-20261026-7f3a"}
    metrics = SimpleNamespace(step_failures=lambda *a, **k: stats, resource_trend=lambda m: [{"task_id": "t1", "mem_max": 1, "rss_max": 1, "inflight_max": 6}],
                              llm_summary=lambda f, m: {"feature": f, "minutes": m, "calls": 0, "sample_ids": []})
    records = SimpleNamespace(recent_logs=lambda m, q=None: [{"id": 5, "message": "openai request failed: status=429"}],
                              request_trace=lambda r: {"result": None, "logs": [], "llm_calls": []})
    members = Recorder()
    members.returns = {"is_member": False}
    return SimpleNamespace(coord=coord, chat=chat, docs=docs, adjust=adjust, knowledge=knowledge, incidents=incidents, metrics=metrics,
                           records=records, drills=Recorder(), members=members)


def ctx_with(llm):
    s = load({"AI_DB_URL": "mysql://u:p@h/ai_prod", "Admin_Telegram_ID": "1"})
    tg = Recorder()
    tg.returns = {"send": 555, "edit": True, "broadcast": [(100, 555)]}
    queued = []
    ctx = Ctx(settings=s, repos=fake_repos(), telegram=tg, task_id="abcdef123", enqueue=lambda e, nb=None: queued.append((e, nb)))
    ctx.llm = llm
    return ctx, tg, queued


REPORT = {"summary": "영수증 luna_call 실패 4/9 (5분)", "cause_text": "OpenAI 분당 요청 한도 초과", "resolution_text": "동시 호출 상한 축소",
          "step_id": "luna_call", "confidence": "high", "evidence_ids": ["kn:KN_OPENAI_RATE_LIMIT@v1"], "knowledge_ids": ["KN_OPENAI_RATE_LIMIT"],
          "policy_key": "llm.max_concurrency", "unknown": False, "expected_issues": []}
PROPOSAL = {"value": 3, "ttl_min": 30, "reason": "동시 최대 6", "evidence_ids": []}


def test_alert_to_report_with_proposal():
    ctx, tg, _ = ctx_with(FakeChat(structured=[REPORT, PROPOSAL]))
    out = run_event(ctx, new_event("alert.pipeline", {"alert_key": "receipt.luna_call.rate_limit_exceeded", "feature": "receipt",
                                                      "step_id": "luna_call", "reason": "http_4xx", "summary": "429 급증", "detected_at": 0}))
    assert out["outcome"] == "ok", out
    assert out["node_path"] == ["ingest", "route", "diagnose", "chain", "propose", "publish"]
    sent = [c for c in tg.calls if c[0] in ("send", "broadcast")]
    assert len(sent) == 1 and sent[0][0] == "broadcast"
    text = sent[0][1][0]
    assert "⚙️ 조정 제안 · LLM 동시 호출 상한(llm.max_concurrency)\n제한 없음 → 3 · 30분 유지" in text and sent[0][1][1]
    assert text.index("⚙️ 조정 제안") < text.index("↩ 답장하면")
    assert ("link_message", (555, 100, "incident", "I-20261026-7f3a"), {}) in ctx.repos.coord.calls


ANSWER = {"answer_text": "정리본을 만들겠습니다", "evidence_ids": [], "intent": "knowledge_add", "target_knowledge_id": None, "target_doc": None,
          "incident_id": None, "edit_request": "429 한도 초과 대응을 지식으로", "expected_issues": []}
ITEM = {"title": "한도 초과 새 대응", "features": [], "step_ids": ["luna_call"], "match": {"step_id": ["luna_call"], "status": [429]},
        "symptom": "429 급증", "cause": "한도 초과", "resolution": "한도 상향 요청", "checks": [], "policy_key": None, "alert": "diagnose",
        "severity": "medium"}
DRAFT = {"op": "add", "target_id": None, "knowledge": ITEM, "reason": "운영자 요청", "source_refs": [], "duplicate_of": "KN_OPENAI_RATE_LIMIT"}


def test_chat_to_knowledge_draft():
    ctx, tg, _ = ctx_with(FakeChat(structured=[ANSWER, DRAFT]))
    out = run_event(ctx, new_event("user.message", {"message_id": 7, "text": "이걸로 지식 추가해줘"}, source="telegram", from_id=1, chat_id=100))
    assert out["node_path"] == ["ingest", "route", "chat", "chain", "knowledge", "publish"], out
    sent = [c for c in tg.calls if c[0] == "send"]
    assert [s[1][1][:6] for s in sent] == ["정리본을 만", "📝 지식 추"]
    assert sent[1][1][2] == [[("저장", "ks:D-20261026-cd34"), ("취소", "kc:D-20261026-cd34")]]
    assert ("fill_bot_message", (11, 555), {}) in ctx.repos.chat.calls


def test_button_save():
    ctx, tg, _ = ctx_with(FakeChat())
    out = run_event(ctx, new_event("action.knowledge_save", {"draft_id": "D-1", "callback_id": "cb", "message_id": 3}, source="telegram",
                                   from_id=1, chat_id=100))
    assert out["node_path"] == ["ingest", "route", "action", "publish"]
    assert ("answer_callback", ("cb", "처리 중"), {}) in tg.calls and any(c[0] == "send" and c[1][1].startswith("저장했습니다") for c in tg.calls)


def test_drill_incident_reply_gets_notice_only():
    ctx, tg, _ = ctx_with(FakeChat())
    ctx.repos.incidents.returns = {"get": {"id": "I-1", "drill": True}}
    out = run_event(ctx, new_event("user.message", {"message_id": 8, "text": "원인 뭐야", "reply_to_message_id": 3, "reply_ref": "incident:I-1"},
                                   source="telegram", from_id=1, chat_id=100))
    sent = [c for c in tg.calls if c[0] == "send"]
    assert out["outcome"] == "ok" and len(sent) == 1 and sent[0][1][1].startswith("훈련 장애라")
    assert not any(c[0] == "save_turn" for c in ctx.repos.chat.calls)
