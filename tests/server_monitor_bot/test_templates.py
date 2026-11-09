# 텔레그램 메시지 템플릿: 종류별 출력 고정(정상·예외), KST, 한글(코드), HTML 이스케이프·접기·길이 제한.
import json
from datetime import datetime
from types import SimpleNamespace

from app.Server_Monitor_Bot.channels.telegram import render as r
from app.Server_Monitor_Bot.channels.telegram.client import TelegramClient

AT = 1793079180.0
SIG = {"feature": "receipt", "step_id": "image_fetch", "reason": "http_4xx", "status": 404, "error_code": None, "fails": 9, "total": 17}
KN = {"id": "KN_IMAGE_NOT_FOUND", "version": 1, "title": "이미지 객체 없음(404)", "severity": "medium", "cause": "업로드 누락·객체 키 오류",
      "resolution": "업로드 경로·키 생성 확인", "checks": ["최근 이미지 다운로드 실패 중 404 비율", "샘플 요청의 이미지 호스트"]}


def test_labels_time_and_numbers():
    assert r.kst(AT) == "14:33" and r.kst(AT, "%m-%d %H:%M") == "10-27 14:33"
    assert r.step("image_fetch") == "이미지 다운로드(image_fetch)" and r.step("new_step") == "new_step" and r.step(None) == "-"
    assert r.cause("http_4xx", 404) == "파일 없음(404)" and r.cause("connect") == "연결 실패(connect)"
    assert r.cause("http_4xx", 429, "rate_limit_exceeded") == "분당 한도 초과(429 rate_limit_exceeded)"
    assert r.cause("weird") == "weird" and r.cause(status=418) == "418" and r.cause() == "-"
    assert r.knob("llm.max_concurrency") == "LLM 동시 호출 상한(llm.max_concurrency)" and r.value("llm.max_concurrency", 0) == "제한 없음"
    assert r.num(24861) == "24,861" and r.ms(820) == "820ms" and r.ms(4100) == "4.1s" and r.ms(None) == "-"
    assert r.clean("reason=connect route=None(None) 4ms") == "reason=connect 4ms"


def test_alerts():
    item = {"signal": SIG, "at": AT, "rank": 3, "samples": ["e3501e330fcc", "38b95c7272d8", "zzz"]}
    assert r.alert_known(item, KN) == (
        "🟠 영수증 · 이미지 다운로드(image_fetch) 실패\nPOST /receipt → IMAGE_FETCH_FAILED · 파일 없음(404) 9/17건 · 최근 5분 · 14:33\n"
        "코드: app/features/receipt/image.py · download()\n\n"
        "알려진 장애: 이미지 객체 없음(404) · KN_IMAGE_NOT_FOUND v1\n원인: 업로드 누락·객체 키 오류\n조치: 업로드 경로·키 생성 확인\n"
        "확인할 곳: 최근 이미지 다운로드 실패 중 404 비율, 샘플 요청의 이미지 호스트\n샘플: /trace e3501e330fcc · /trace 38b95c7272d8\n\n"
        "↩ 답장하면 이 장애로 대화합니다")
    assert r.alert_summary({**item, "held": 12, "drill": True, "samples": []}) == (
        "🔴 [훈련] 영수증 · 이미지 다운로드(image_fetch) 실패 (재발)\n"
        "POST /receipt → IMAGE_FETCH_FAILED · 파일 없음(404) 9/17건 · 최근 5분 · 14:33 · 지난 10분 +12건\n"
        "코드: app/features/receipt/image.py · download()\n\n↩ 답장하면 이 장애로 대화합니다")
    assert "확인할 곳:" not in r.alert_known(item, {**KN, "checks": []})
    size = {**SIG, "step_id": "image_validate", "reason": "size", "feature": "taxi_meter"}
    assert r.where_lines(size)[0] == "POST /taxi_meter → IMAGE_SIZE_INVALID" and r.where_lines(size)[1].startswith("코드: app/features/taxi_meter/image.py")
    assert r.where_lines({**SIG, "step_id": "image_validate", "reason": "wrong_type"})[0].endswith("IMAGE_TYPE_INVALID")
    assert r.where_lines({"feature": "receipt", "step_id": "new_step"}) == ["POST /receipt"]


def ev(eid, summary, data):
    return {"id": eid, "summary": summary, "data": json.dumps(data, ensure_ascii=False)}


EVIDENCE = [
    ev("metric:receipt.image_fetch@14:33", "receipt.image_fetch 최근 15분 실패 17/17", {
        "minutes": 15, "now": {"total": 17, "fails": 17, "reasons": [{"reason": "connect", "status": None, "n": 8},
                                                                       {"reason": "http_4xx", "status": 404, "n": 9}]},
        "before": {"total": 0, "fails": 0}}),
    ev("req:aaa", "receipt fail code=X step=image_fetch reason=connect route=None(None) 4ms",
       {"result": {"step_id": "image_fetch", "reason": "connect", "status": None}}),
    ev("req:bbb", "receipt fail", {"result": {"step_id": "image_fetch", "reason": "connect", "status": None}}),
    ev("kn:KN_IMAGE_HOST_SLOW@v1", "KN_IMAGE_HOST_SLOW 이미지 호스트 지연", [{"title": "이미지 호스트 지연"}, {"title": "이미지 객체 없음"}]),
]
REPORT = {"summary": "17/17건 실패 (5분) · 연결 실패 8건, 파일 없음(404) 9건", "cause_text": "연결 실패와 <404>가 함께 관측",
          "resolution_text": "호스트·포트 확인 & 경로 확인", "step_id": "image_fetch", "confidence": "low", "unknown": True,
          "evidence_ids": ["metric:receipt.image_fetch@14:33", "req:aaa", "req:bbb", "kn:KN_IMAGE_HOST_SLOW@v1"],
          "knowledge_ids": ["KN_IMAGE_HOST_SLOW"]}


def test_diagnosis_html_with_fold():
    text = r.diagnosis(REPORT, EVIDENCE, "receipt", AT, samples=["aaa", "bbb"], reason="connect")
    head, fold = text.split("<b>근거 상세</b>")
    assert head == (
        "🟠 영수증 · 이미지 다운로드(image_fetch) 진단 · 14:33\nPOST /receipt → IMAGE_FETCH_FAILED\n"
        "코드: app/features/receipt/image.py · download()\n17/17건 실패 (5분) · 연결 실패 8건, 파일 없음(404) 9건\n\n"
        "원인(추정) · 확신도 낮음: 연결 실패와 &lt;404&gt;가 함께 관측\n조치: 호스트·포트 확인 &amp; 경로 확인\n예상 문제\n· 근거가 없어 예상 문제는 모릅니다\n샘플: /trace aaa · /trace bbb\n\n근거 요약\n"
        "· 최근 15분 17/17 실패 (직전 0/0) · 연결 실패(connect) 8건, 파일 없음(404) 9건\n"
        "· 샘플 요청 2건 · 이미지 다운로드(image_fetch) 연결 실패(connect)\n· 참고 지식: 이미지 호스트 지연, 이미지 객체 없음\n")
    assert fold.startswith("\n<blockquote expandable>metric:receipt.image_fetch@14:33 — ")
    assert "None" not in fold and fold.rstrip().endswith("↩ 답장하면 이 장애로 대화합니다")
    assert "원인 미확정 — 대화로 알려 주시면 지식으로 저장합니다" in fold
    drill = r.diagnosis({**REPORT, "confidence": "high", "unknown": False}, [], "receipt", AT, drill=True)
    assert drill.startswith("🧪 [훈련] 영수증 · 이미지 다운로드(image_fetch) 진단") and "원인 · 확신도 높음" in drill
    assert "↩" not in drill and "blockquote" not in drill.split("근거 상세")[0]


def test_comma_joined_ids_and_hard_limit():
    joined = [{"id": "llm:1,llm:2", "summary": "receipt 호출 2건 route=None(None)", "data": "not json"}]
    text, is_html = r.chat_answer("x", joined, ["llm:2"])
    assert is_html and "<blockquote expandable>llm:2 — receipt 호출 2건" in text and "None" not in text
    assert r.summary_lines(joined, ["llm:1"]) == ["receipt 호출 2건"]
    huge = r.diagnosis({**REPORT, "summary": "x" * 5000}, EVIDENCE, "receipt", AT)
    amp, _ = r.chat_answer("&" * 1500, EVIDENCE, REPORT["evidence_ids"])
    assert len(huge) <= r.LIMIT and len(amp) <= r.LIMIT and huge.count("<blockquote expandable>") == huge.count("</blockquote>") == 1
    merged = r.attach(r.diagnosis({**REPORT, "summary": "y" * 3500}, EVIDENCE * 20, "receipt", AT), "⚙️ 제안 " * 40, True)
    assert len(merged) <= r.LIMIT and merged.index("⚙️ 제안") < merged.index("↩ 답장하면")
    item = {"signal": {**SIG, "fails": 1234, "total": 5678}, "at": AT, "rank": 2}
    assert "1,234/5,678건" in r.alert_summary(item)


def test_html_is_clipped_inside_fold():
    long = [ev(f"req:{i:04d}", "x" * 300, {}) for i in range(40)]
    text = r.diagnosis({**REPORT, "evidence_ids": [e["id"] for e in long]}, long, "receipt", AT)
    assert len(text) <= r.LIMIT + 200 and "…</blockquote>" in text and text.rstrip().endswith("대화합니다")
    assert r.clip("가" * 5000).endswith("…") and len(r.clip("가" * 5000)) == r.LIMIT


def test_chat_answer_and_attach():
    assert r.chat_answer("답 <b>", [], []) == ("답 <b>", False)
    html_text, is_html = r.chat_answer("답 <b>", EVIDENCE, ["kn:KN_IMAGE_HOST_SLOW@v1"])
    assert is_html
    assert html_text.startswith("답 &lt;b&gt;\n<b>근거 1개</b>\n<blockquote expandable>kn:KN_IMAGE_HOST_SLOW@v1 — ")
    report = r.diagnosis(REPORT, EVIDENCE, "receipt", AT)
    merged = r.attach(report, "⚙️ 조정 제안 · x <y>", True)
    assert merged.index("⚙️ 조정 제안 · x &lt;y&gt;") < merged.index("↩ 답장하면") and r.attach("알림", "제안") == "알림\n\n제안"


def test_adjust_and_knowledge():
    assert r.adjust_proposal("llm.max_concurrency", 0, 3, 30, "동시 최대 6") == (
        "⚙️ 조정 제안 · LLM 동시 호출 상한(llm.max_concurrency)\n제한 없음 → 3 · 30분 유지\n근거: 동시 최대 6")
    assert r.adjust_applied("llm.max_concurrency", None, 3, 30, AT, 10) == (
        "⚙️ 조정 적용 · LLM 동시 호출 상한(llm.max_concurrency)\n제한 없음 → 3 · 14:33까지 (30분)\n10분 뒤 효과를 보고합니다")
    v = r.adjust_verdict("improved", "llm.max_concurrency", {"feature": "receipt", "step_id": "luna_call"},
                         {"fails": 4, "calls": 9, "p95_ms": 12000}, {"fails": 0, "calls": 12, "p95_ms": 800}, AT, "429가 사라짐")
    assert v.startswith("✅ 조정 효과 · 개선 · 유지\nLLM 동시 호출 상한(llm.max_concurrency)\n영수증 OpenAI 호출(luna_call) 실패 4/9 → 0/12\np95 12.0s → 800ms · ")
    assert v.endswith("\n14:33 자동 원복\n429가 사라짐")
    assert r.adjust_expired([{"key": "image.max_side_px", "adjustment_id": "A-1"}]) == (
        "⏱ 조정 만료 · 원래 값으로 되돌렸습니다\n· 이미지 최대 변 길이(image.max_side_px) (A-1)")
    item = {"title": "한도 초과", "match": {"step_id": ["luna_call"], "status": [429]}, "cause": "c", "resolution": "r", "checks": []}
    assert r.knowledge_draft("add", item, "D-1", "요청", None, 24) == (
        "📝 지식 추가 정리본 · 24시간 유효\n제목: 한도 초과\n신호: step_id OpenAI 호출(luna_call) · status 한도 초과(429)\n"
        "원인: c\n조치: r\n이유: 요청\n정리본 D-1")
    assert r.knowledge_saved("update", {"title": "t", "id": "KN_A", "version": 2}, False).endswith("검색 반영은 잠시 뒤 다시 시도합니다")


def test_reports_and_system():
    daily = r.daily("2026-10-26", {"llm": [{"feature": "receipt", "calls": 1200, "fails": 2, "retried": None, "input_tokens": 12345,
                                             "output_tokens": 2345, "avg_ms": 820, "max_ms": 4100}],
                                    "fail_reasons": [{"feature": "receipt", "step_id": "image_fetch", "reason": "http_4xx", "status": 404, "n": 9}],
                                    "monitor": {"runs": 3, "failed": 0, "tokens": 24861}, "health": None})
    assert daily == ("📊 일일 리포트 · 2026-10-26\n\nLLM 호출\n· 영수증: 1,200회 · 실패 2 · 재시도 0 · 토큰 12,345/2,345 · 평균 820ms · 최대 4.1s\n\n"
                     "실패 사유 상위\n· 영수증 이미지 다운로드(image_fetch) 파일 없음(404) 9건 · IMAGE_FETCH_FAILED\n\n"
                     "모니터: 실행 3회(실패 0) · 토큰 24,861 · 미발송 0 · 재시작 0 · 버린 기록 0")
    assert "· 호출 없음" in r.daily("d", {"llm": [], "fail_reasons": [], "monitor": None, "health": None})
    assert r.unmatched([{"feature": "taxi_meter", "step_id": "luna_parse", "reason": "not_json", "status": None, "error_code": None, "n": 3}]) == (
        "🧩 미분류 신호 · 최근 24시간\n어떤 지식에도 맞지 않음 — 지식 추가 후보\n· 미터기 응답 해석(luna_parse) JSON 아님(not_json) 3건 · JUDGE_PARSE_FAILED")
    assert r.seed({"inserted": 23}, [("KN_X", ["bad"])], "v1.2") == "📚 지식 시드 적재 · v1.2\n추가 23 · 갱신 0 · 색인 실패 0\n⚠️ 깨진 항목 KN_X: bad"
    assert r.budget_reached("receipt.luna_call.x", 4, 9).endswith("receipt.luna_call.x 4/9")


def test_commands():
    assert r.status(True, {"incidents": 1, "flags": 0, "knowledge": 23, "tokens": 24861, "drill_tokens": 0}, True, "494bc5") == (
        "✅ 상태 정상\n열린 장애 1건 · 활성 조정 0개 · 지식 23개\n오늘 토큰 24,861 (훈련 0)\n리더 예 · 태스크 494bc5")
    assert r.status(False, {}, False, "494bc5", "OperationalError").startswith("⚠️ 상태 확인 실패\nDB 확인 실패: OperationalError")
    assert r.flags([]) == "활성 조정 없음"
    assert r.flags([{"key": "llm.max_concurrency", "value": 3, "expires_at": datetime(2026, 10, 27, 5, 33), "set_by": "S4b"}]) == (
        "⚙️ 활성 조정\n· LLM 동시 호출 상한(llm.max_concurrency) = 3 · 14:33까지 · S4b")
    assert r.knowledge_list([]) == "지식이 없습니다" and r.knowledge_list([{"title": "t", "id": "KN_A", "version": 1}]) == "📚 지식 1개\n· t · KN_A v1"
    text, buttons = r.member_list([{"user_id": 7, "chat_id": 70, "name": None, "username": None, "requested_at": datetime(2026, 10, 27, 3, 0)}])
    assert text == "👥 가입 요청 1건\n· - @- · user 7 · chat 70 · 10-27 12:00" and buttons == [[("승인 · -", "ma:7"), ("거절", "mr:7")]]
    assert r.say("drill_started", run_id="R-1") == "🧪 훈련 시작 · R-1\n합성 요청 20건 · 15초 간격\n끝나면 /drill stop"
    assert "/request" in r.HELP and "(관리자)" in r.HELP


def test_client_html_mode_and_plain_fallback():
    calls = []

    class Http:
        def post(self, url, json, timeout):
            calls.append(json)
            ok = json.get("parse_mode") != "HTML" or len(calls) > 1
            return SimpleNamespace(status_code=200 if ok else 400,
                                   json=lambda: {"ok": ok, "result": {"message_id": 5}} if ok else {"ok": False})

    tg = TelegramClient("t", (1,), "[DEV]", http=Http())
    assert tg.send(1, "a <b>x</b> &amp; b", html=True) == 5
    assert calls[0]["parse_mode"] == "HTML" and calls[0]["text"] == "[DEV] a <b>x</b> &amp; b"
    assert "parse_mode" not in calls[1] and calls[1]["text"] == "[DEV] a x & b"

    class Down:
        def post(self, url, json, timeout):
            calls.append(json)
            return SimpleNamespace(status_code=502, json=lambda: {"ok": False})

    calls.clear()
    tg = TelegramClient("t", (1,), "[DEV]", http=Down())
    tg.call = lambda method, payload, timeout=10, retry=True: (setattr(tg, "last_status", 502), calls.append(payload))[0]
    assert tg.send(1, "<b>x</b>", html=True) is None and len(calls) == 1
