# 텔레그램 메시지 템플릿: 모든 알림·리포트·응답 문구와 공통 규칙(KST, 한글(코드), 숫자 구분, 근거 접기 HTML, 길이 제한).
import hashlib
import html
import json
import re
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
LIMIT = 4000
FEATURES = {"receipt": "영수증", "taxi_meter": "미터기", "congestion": "혼잡도"}
STEPS = {"request_validate": "요청 검증", "image_fetch": "이미지 다운로드", "image_validate": "이미지 검사", "model_ready": "모델 준비",
         "pipe_ocr": "OCR", "pipe_judge": "로컬 판독", "queue_wait": "대기열", "luna_call": "OpenAI 호출", "luna_parse": "응답 해석",
         "respond": "응답", "unhandled": "처리되지 않은 오류", "fallback": "대체 처리", "congestion_judge": "판정 출력 검증"}
REASONS = {"connect": "연결 실패", "timeout": "시간 초과", "redirect": "리다이렉트", "bad_url": "잘못된 URL", "http_4xx": "4xx 응답",
           "http_5xx": "5xx 응답", "size": "크기 초과", "not_image": "이미지 아님", "wrong_type": "jpeg·png 아님", "key_missing": "API 키 없음",
           "model_down": "모델 중단", "shape": "응답 형식 오류", "expired": "대기 시간 초과", "concurrency_wait": "동시 호출 대기 초과",
           "auth": "인증 실패", "unreadable": "판독 불가", "not_json": "JSON 아님", "not_amount": "금액 아님",
           "transfer_not_done": "이체 미완료", "judge_fallback": "판독 대체", "device_off": "장치 꺼짐",
           "id_mismatch": "판정 id 불일치", "missing_key": "API 키 없음", "empty_output": "빈 응답"}
STATUS = {400: "잘못된 요청", 401: "인증 실패", 403: "접근 거부", 404: "파일 없음", 408: "시간 초과", 413: "너무 큼", 429: "한도 초과",
          500: "서버 오류", 502: "게이트웨이 오류", 503: "서비스 불가", 504: "게이트웨이 시간 초과"}
ERRORS = {"rate_limit_exceeded": "분당 한도 초과", "insufficient_quota": "크레딧 소진", "invalid_api_key": "API 키 오류",
          "model_not_found": "모델 없음", "context_length_exceeded": "입력 길이 초과", "server_error": "OpenAI 서버 오류"}
KNOBS = {"llm.max_concurrency": "LLM 동시 호출 상한", "image.max_side_px": "이미지 최대 변 길이", "route.force_luna": "luna 강제 전환",
         "modal.wait_s": "Modal 대기 시간"}
ICON = {"high": "🔴", "medium": "🟠", "low": "🟡", 3: "🔴", 2: "🟠", 1: "🟡"}
CONF = {"high": "높음", "medium": "보통", "low": "낮음"}
VERDICT = {"improved": ("✅", "개선 · 유지"), "no_effect": ("➖", "효과 없음"), "pending": ("⏸", "판정 보류 · 조정 뒤 호출 5건 미만"), "worse": ("⚠️", "악화 · 원래 값으로 되돌렸습니다")}
OPS = {"add": "추가", "update": "수정", "delete": "삭제"}
ENDPOINTS = {"receipt": "POST /receipt", "taxi_meter": "POST /taxi_meter", "congestion": "POST /congestion-analyses"}
APP_CODES = {"request_validate": "REQUEST_INVALID", "image_fetch": "IMAGE_FETCH_FAILED", "model_ready": "MODEL_NOT_READY",
             "queue_wait": "QUEUE_TIMEOUT", "luna_call": "UPSTREAM_FAILED", "luna_parse": "JUDGE_PARSE_FAILED", "unhandled": "INTERNAL",
             "congestion_judge": "CONGESTION_PARSE_FAILED"}
CODE_AT = {"request_validate": "app/main.py · request_invalid()", "image_fetch": "app/features/{f}/image.py · download()",
           "image_validate": "app/features/{f}/image.py · download()·load_image()",
           "model_ready": "app/llm/router.py · ready() / app/llm/providers/modal_gpu.py · ModalEngine.prepare()",
           "queue_wait": "app/inner_queue/scheduler.py · Scheduler.submit()",
           "luna_call": "app/llm/providers/openai.py · OpenAIEngine.complete()",
           "luna_parse": "app/features/{f}/pipeline.py · model_api_run()", "pipe_ocr": "app/llm/providers/ocr.py · OcrEngine.read()",
           "pipe_judge": "app/features/{f}/pipeline.py · model_pipe_run()", "unhandled": "app/main.py · unhandled()",
           "fallback": "app/features/{f}/main.py · run_gpu()"}
CODE_AT_FEATURE = {("congestion", "luna_call"): "app/features/congestion/model_client.py · _call()",
                   ("congestion", "model_ready"): "app/features/congestion/model_client.py · _call()",
                   ("congestion", "congestion_judge"): "app/features/congestion/spot_extractor.py · assess_spot()"}
REPLY = "↩ 답장하면 이 장애로 대화합니다"
DRILL_NOTICE = "훈련 장애라 대화·지식 저장 대상이 아닙니다"
CHAT_FAILED = "답을 만들지 못했습니다. 질문을 조금 바꿔 다시 물어봐 주세요."
UNVERIFIED = "(근거 미확인)"
FOLD = "<blockquote expandable>"
HELP = """📖 명령
장애·상태
· /status 상태 · /flags 활성 조정 · /knowledge [검색어] 지식 목록
· /trace <request_id> 요청 추적
훈련 (dev·local)
· /drill start <id> · /drill stop · /drill guess <원인 설명> (관리자)
팀원
· /join 가입 요청 · /request 대기 중인 가입 요청 (관리자)
대화
· 그 밖의 메시지는 챗봇이 답합니다
· 알림·리포트에 답장하면 그 장애로 대화합니다
· 지식 추가·수정·삭제는 대화로 요청하면 정리본과 [저장][취소]가 옵니다
문서 (관리자)
· 파일(md·txt·pdf·docx, 20MB 이하)을 보내면 바로 저장합니다
· /docs 문서 목록 · /docs <이름> 절 목록과 [삭제]
· 절 정정은 대화로 요청하면 고친 본문과 [저장][취소]가 옵니다(저장소 문서는 git에서 수정)"""
TEXT = {
    "draft_cancelled": "정리본을 취소했습니다",
    "draft_gone": "이미 처리했거나 없는 정리본입니다",
    "draft_failed": "정리하지 못했습니다: {reasons}",
    "adjust_gone": "이미 처리했거나 없는 조정입니다 ({adj_id})",
    "adjust_rejected": "조정을 거절했습니다: {knob}",
    "adjust_none": "되돌릴 활성 조정이 없습니다",
    "adjust_rolled_back": "원래 값으로 되돌렸습니다: {knob}",
    "adjust_retry": "적용하지 못했습니다. 잠시 뒤 [승인]을 다시 눌러 주세요",
    "drill_not_found": "시나리오를 찾지 못했습니다. 예: /drill start S-7a1c",
    "drill_running": "이미 진행 중인 훈련이 있습니다. /drill stop 뒤에 시작하세요",
    "drill_no_image": "{scenario}에 image_url(개인정보 없는 dev 테스트 이미지)을 넣거나 env DRILL_IMAGE_URL을 먼저 넣어 주세요",
    "drill_started": "🧪 훈련 시작 · {run_id}\n합성 요청 20건 · 15초 간격\n끝나면 /drill stop",
    "drill_none": "진행 중인 훈련이 없습니다",
    "drill_stopped_c": "🧪 훈련 종료 · {run_id}\n묶음 C(오탐 확인) · 채점 1행",
    "drill_stopped": "🧪 훈련 종료 · {run_id}\n비교 실행 9건을 60초 간격으로 예약했습니다",
    "drill_guess_empty": "진행 중인 훈련이 없거나 원인 설명이 비었습니다. 예: /drill guess OpenAI 한도 초과",
    "drill_guess_full": "추정은 두 번까지 기록합니다",
    "drill_guessed": "{n}회차 추정을 기록했습니다",
    "member_requested": "가입 요청을 보냈습니다. 관리자 승인 후 사용할 수 있습니다",
    "member_pending": "이미 승인 대기 중입니다",
    "member_exists": "이미 등록된 사용자입니다",
    "member_none": "대기 중인 가입 요청이 없습니다",
    "member_bad": "잘못된 요청입니다",
    "member_done": "이미 처리된 요청입니다",
    "member_approved": "✅ 승인했습니다: {name}",
    "member_rejected": "거절했습니다: {name}",
    "member_welcome": "✅ 가입이 승인되었습니다. /help로 명령을 확인하세요",
    "member_denied": "가입이 거절되었습니다. 다시 요청하려면 /join",
    "no_flags": "활성 조정 없음",
    "no_knowledge": "지식이 없습니다",
    "denied": "권한이 없습니다",
    "stage_blocked": "{stage}에서는 훈련을 할 수 없습니다",
    "disabled": "이 기능은 {stage}에서 꺼져 있습니다",
    "failed": "⚠️ {reason}",
    "processing": "처리 중",
    "fast_edit": "정리본을 만들겠습니다",
    "queue_ack": "🔎 접수 · 대기 {n}번째",
    "queue_full": "⏳ 대기열이 가득 찼습니다({n}건) · 잠시 뒤 다시 보내 주세요",
    "queue_dup": "같은 질문을 처리 중입니다",
    "doc_failed": "⚠️ 문서를 저장하지 못했습니다: {reason}",
    "doc_none": "문서가 없습니다",
    "doc_missing": "문서를 찾지 못했습니다: {doc_id}",
    "doc_repo": "저장소 문서({doc_id})는 텔레그램으로 고치거나 지울 수 없습니다. git에서 수정해 배포하세요",
    "doc_deleted": "🗑 문서를 삭제했습니다: {doc_id} · {n}절",
    "doc_target": "고칠 문서 절을 찾지 못했습니다. /docs <이름>으로 절을 확인한 뒤 다시 말해 주세요",
}


def say(key: str, **kw) -> str:
    return TEXT[key].format(**kw)


def kst(ts: float | None = None, fmt: str = "%H:%M") -> str:
    return datetime.fromtimestamp(ts if ts is not None else datetime.now().timestamp(), KST).strftime(fmt)


def num(n) -> str:
    return f"{int(n or 0):,}"


def ms(x) -> str:
    if not x:
        return "-"
    return f"{x / 1000:.1f}s" if x >= 1000 else f"{int(x)}ms"


def feature(f) -> str:
    return FEATURES.get(f, f or "-")


def step(s) -> str:
    return f"{STEPS[s]}({s})" if s in STEPS else (s or "-")


def knob(k) -> str:
    return f"{KNOBS[k]}({k})" if k in KNOBS else (k or "-")


def cause(reason=None, status=None, error_code=None) -> str:
    if error_code:
        label = ERRORS.get(error_code)
        code = f"{status} {error_code}" if status else error_code
        return f"{label}({code})" if label else code
    if status:
        try:
            status = int(status)
        except (TypeError, ValueError):
            return str(status)
        return f"{STATUS[status]}({status})" if status in STATUS else str(status)
    if reason:
        return f"{REASONS[reason]}({reason})" if reason in REASONS else str(reason)
    return "-"


def value(k, v) -> str:
    if v in (0, None, False) and k == "llm.max_concurrency":
        return "제한 없음"
    return str(v)


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=False)


def clean(text) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\S*None\S*", "", str(text or ""))).strip()


def clip(text: str, limit: int = LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def esc_clip(raw: str, budget: int) -> str:
    cut = len(raw)
    while cut > 0 and len(esc(raw[:cut])) > budget:
        cut = int(cut * 0.9)
    return esc(raw[:cut]) + ("…" if cut < len(raw) else "")


def fit(text: str) -> str:
    while len(text) > LIMIT and FOLD in text:
        start = text.index(FOLD) + len(FOLD)
        stop = text.index("</blockquote>", start)
        inner = [x for x in text[start:stop].split("\n") if x != "…"]
        if not inner:
            break
        text = text[:start] + "\n".join(inner[:-1] + ["…"]) + text[stop:]
    return text


def code(text: str) -> str:
    return re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)


def html_message(body: list[str], fold_title: str | None = None, fold_lines: list[str] | None = None, tail: list[str] | None = None) -> str:
    end = "\n".join(esc(line) for line in tail or [])
    head = code(esc_clip("\n".join(body), LIMIT - len(end) - 300))
    if not fold_lines:
        return "\n".join(x for x in (head, end) if x)
    fold = f"<b>{esc(fold_title or '근거')}</b>\n{FOLD}" + "\n".join(esc(line) for line in fold_lines) + "</blockquote>"
    return fit("\n".join(x for x in (head, fold, end) if x))


def app_code(step_id, reason=None) -> str | None:
    if step_id == "image_validate":
        return "IMAGE_SIZE_INVALID" if reason == "size" else "IMAGE_TYPE_INVALID"
    return APP_CODES.get(step_id)


def code_at(feature_, step_id) -> str | None:
    at = CODE_AT_FEATURE.get((feature_, step_id)) or CODE_AT.get(step_id)
    return at.format(f=feature_ or "{feature}") if at else None


def where_lines(sig: dict, detail: str | None = None) -> list[str]:
    head = " → ".join(x for x in (ENDPOINTS.get(sig.get("feature")), app_code(sig.get("step_id"), sig.get("reason"))) if x)
    at = code_at(sig.get("feature"), sig.get("step_id"))
    first = " · ".join(x for x in (head, detail) if x)
    return [x for x in (first, f"코드: {at}" if at else None) if x]


def samples_line(ids) -> str | None:
    ids = [i for i in ids or [] if i][:2]
    return "샘플: " + " · ".join(f"/trace {i}" for i in ids) if ids else None


def title(sig: dict, word: str = "실패", drill: bool = False) -> str:
    return f"{'[훈련] ' if drill else ''}{feature(sig.get('feature'))} · {step(sig.get('step_id'))} {word}".strip()


def count_line(sig: dict, at: float | None, held: int = 0) -> str:
    parts = [f"{cause(sig.get('reason'), sig.get('status'), sig.get('error_code'))} {num(sig.get('fails'))}/{num(sig.get('total'))}건",
             "최근 5분", kst(at)]
    if held:
        parts.append(f"지난 10분 +{num(held)}건")
    return " · ".join(parts)


def alert_known(item: dict, k: dict) -> str:
    sig = item["signal"]
    lines = [f"{ICON.get(k.get('severity'), '🟠')} {title(sig, drill=bool(item.get('drill')))}",
             *where_lines(sig, count_line(sig, item.get("at"))), "", f"알려진 장애: {k['title']} · {k['id']} v{k['version']}", f"원인: {k['cause']}",
             f"조치: {k['resolution']}"]
    if k.get("checks"):
        lines.append("확인할 곳: " + ", ".join(k["checks"]))
    lines += [x for x in (samples_line(item.get("samples")),) if x] + ["", REPLY]
    return clip("\n".join(lines))


def alert_summary(item: dict) -> str:
    sig = item["signal"]
    word = "실패 (재발)" if item.get("held") else "실패"
    lines = [f"{ICON.get(item.get('rank'), '🟠')} {title(sig, word, bool(item.get('drill')))}",
             *where_lines(sig, count_line(sig, item.get("at"), item.get("held") or 0))]
    lines += [x for x in (samples_line(item.get("samples")),) if x] + ["", REPLY]
    return clip("\n".join(lines))


def _data(e: dict):
    try:
        return json.loads(e.get("data") or "null")
    except (TypeError, ValueError):
        return None


def parts(e: dict) -> list[str]:
    return [x.strip() for x in str(e.get("id") or "").split(",") if x.strip()]


def cited_evidence(evidence: list[dict], cited: list[str]) -> list[dict]:
    out = []
    for i in cited:
        e = next((x for x in evidence if i in parts(x)), None)
        if e is not None and e not in out:
            out.append(e)
    return out


def _evidence_line(e: dict) -> str | None:
    kind = str(e.get("id", "")).split(":", 1)[0]
    d = _data(e)
    if kind == "metric" and isinstance(d, dict):
        now, before = d.get("now") or {}, d.get("before") or {}
        span = f"최근 {d['minutes']}분 " if d.get("minutes") else ""
        if not now.get("total") and not before.get("total"):
            return None
        line = f"{span}{num(now.get('fails'))}/{num(now.get('total'))} 실패 (직전 {num(before.get('fails'))}/{num(before.get('total'))})"
        reasons = ", ".join(f"{cause(r.get('reason'), r.get('status'))} {num(r.get('n'))}건" for r in now.get("reasons") or [])
        return f"{line} · {reasons}" if reasons else line
    if kind == "req" and isinstance(d, dict) and isinstance(d.get("result"), dict):
        r = d["result"]
        return f"샘플 요청 {parts(e)[0][4:]}: {step(r.get('step_id'))} {cause(r.get('reason'), r.get('status'), r.get('error_code'))}"
    titles = [str(k.get("title")) for k in d[:3] if isinstance(k, dict) and k.get("title")] if kind == "kn" and isinstance(d, list) else []
    if titles:
        return "참고 지식: " + ", ".join(titles)
    return clean(e.get("summary")) or None


def evidence_line(e: dict) -> str | None:
    try:
        return _evidence_line(e)
    except (TypeError, ValueError, KeyError, AttributeError):
        return clean(e.get("summary")) or None


def summary_lines(evidence: list[dict], cited: list[str], limit: int = 3) -> list[str]:
    first = cited_evidence(evidence, cited)
    order = first + [e for e in evidence if e not in first]
    lines, kinds = [], set()
    for e in order:
        kind = str(e.get("id", "")).split(":", 1)[0]
        line = evidence_line(e)
        if not line or kind in kinds and kind != "metric":
            continue
        if kind == "req":
            same = [x for x in order if str(x.get("id", "")).startswith("req:")]
            if len(same) > 1:
                line = f"샘플 요청 {len(same)}건 · " + (line.partition(": ")[2] or line)
        kinds.add(kind)
        lines.append(line)
        if len(lines) == limit:
            break
    return lines


def detail_lines(evidence: list[dict], cited: list[str]) -> list[str]:
    return [f"{', '.join(p for p in parts(e) if p in cited)} — {clean(e.get('summary')) or evidence_line(e) or e.get('tool') or '-'}"
            for e in cited_evidence(evidence, cited)]


def diagnosis(r: dict, evidence: list[dict], feature_: str | None, at: float | None = None, drill: bool = False,
              samples: list | None = None, reason: str | None = None) -> str:
    sig = {"feature": feature_, "step_id": r.get("step_id"), "reason": reason}
    body = [f"{'🧪 [훈련] ' if drill else '🟠 '}{title(sig, '진단')} · {kst(at)}", *where_lines(sig), r["summary"], ""]
    label = "원인" if r.get("confidence") == "high" and not r.get("unknown") else "원인(추정)"
    body += [f"{label} · 확신도 {CONF.get(str(r.get('confidence')), r.get('confidence'))}: {r['cause_text']}"]
    if r.get("resolution_text"):
        body.append(f"조치: {r['resolution_text']}")
    body += ["예상 문제", *issues(r.get("expected_issues"))]
    if samples_line(samples):
        body.append(samples_line(samples))
    lines = summary_lines(evidence, r.get("evidence_ids") or [])
    if lines:
        body += ["", "근거 요약", *[f"· {x}" for x in lines]]
    tail = []
    if r.get("knowledge_ids"):
        tail.append("참고 지식: " + ", ".join(r["knowledge_ids"]))
    if r.get("unknown"):
        tail.append("원인 미확정 — 대화로 알려 주시면 지식으로 저장합니다")
    if not drill:
        tail.append(REPLY)
    return html_message(body, "근거 상세", detail_lines(evidence, r.get("evidence_ids") or []), [""] + tail if tail else None)


def chat_answer(text: str, evidence: list[dict], cited: list[str], memory: list[dict] | None = None) -> tuple[str, bool]:
    lines = detail_lines(evidence, cited) + [f"지난 대화 · {m['출처']} — {clip(clean(m['질문']), 60)}" for m in (memory or [])[:2]]
    if not lines:
        return (html_message([text]), True) if "`" in text else (clip(text), False)
    return html_message([text], f"근거 {len(lines)}개", lines), True


def adjust_proposal(k: str, current, new, ttl: int, reason: str) -> str:
    return "\n".join([f"⚙️ 조정 제안 · {knob(k)}", f"{value(k, current)} → {value(k, new)} · {ttl}분 유지", f"근거: {reason}"])


def attach(text: str, paragraph: str, html_mode: bool = False) -> str:
    extra = esc(paragraph) if html_mode else paragraph
    head, sep, tail = text.rpartition(REPLY)
    out = f"{head}{extra}\n\n{REPLY}{tail}" if sep else f"{text}\n\n{extra}"
    return fit(out) if html_mode else clip(out)


def adjust_applied(k: str, old, new, ttl: int, expires: float, after_min: int) -> str:
    return "\n".join([f"⚙️ 조정 적용 · {knob(k)}", f"{value(k, old)} → {value(k, new)} · {kst(expires)}까지 ({ttl}분)",
                      f"{after_min}분 뒤 효과를 보고합니다"])


def adjust_verdict(v: str, k: str, target: dict, before: dict, after: dict, auto_until: float | None = None, line: str | None = None) -> str:
    icon, word = VERDICT[v]
    lines = [f"{icon} 조정 효과 · {word}", knob(k),
             f"{feature(target.get('feature'))} {step(target.get('step_id'))} 실패 {num(before.get('fails'))}/{num(before.get('calls'))} → "
             f"{num(after.get('fails'))}/{num(after.get('calls'))}",
             f"p95 {ms(before.get('p95_ms'))} → {ms(after.get('p95_ms'))} · {kst()}"]
    if auto_until:
        lines.append(f"{kst(auto_until)} 자동 원복")
    if line:
        lines.append(line)
    return "\n".join(lines)


def adjust_expired(rows: list[dict]) -> str:
    return "\n".join(["⏱ 조정 만료 · 원래 값으로 되돌렸습니다", *[f"· {knob(r['key'])} ({r['adjustment_id']})" for r in rows]])


def signal_text(match: dict) -> str:
    names = {"step_id": step, "reason": lambda r: cause(reason=r), "status": lambda s: cause(status=s), "error_code": lambda c: cause(error_code=c)}
    parts = []
    for key, vals in (match or {}).items():
        vals = vals if isinstance(vals, list) else [vals]
        fn = names.get(key)
        parts.append(f"{key} " + " / ".join(fn(v) if fn else str(v) for v in vals))
    return " · ".join(parts) or "-"


def knowledge_draft(op: str, k: dict, draft_id: str, reason: str, duplicate_of: str | None, ttl_h: int) -> str:
    lines = [f"📝 지식 {OPS[op]} 정리본 · {ttl_h}시간 유효", f"제목: {k.get('title')}", f"신호: {signal_text(k.get('match') or {})}",
             f"원인: {k.get('cause')}", f"조치: {k.get('resolution')}"]
    if k.get("checks"):
        lines.append("확인할 곳: " + ", ".join(k["checks"]))
    lines.append(f"이유: {reason}")
    if duplicate_of:
        lines.append(f"비슷한 지식: {duplicate_of}")
    lines.append(f"정리본 {draft_id}")
    return clip("\n".join(lines))


UNKNOWN_ISSUES = "· 근거가 없어 예상 문제는 모릅니다"


def issues(items: list[dict] | None) -> list[str]:
    return [f"· {i.get('problem')} → {i.get('solution')} ({', '.join(i.get('evidence_ids') or [])})" for i in items or []] or [UNKNOWN_ISSUES]


def with_issues(text: str, items: list[dict] | None) -> str:
    return f"{text}\n\n예상 문제\n" + "\n".join(issues(items)) if items else text


def doc_saved(r) -> str:
    lines = [f"📄 문서 저장 · {r.doc_id}", f"{r.title}", f"새 절 {r.added} · 같음 {r.same} · 겹침 {len(r.overlap)} · 삭제 {r.removed}"]
    lines += [f"· 겹침: {x}" for x in r.overlap[:5]]
    if r.overlap:
        lines.append("겹친 절이 틀렸거나 필요 없으면 대화로 정정해 주세요")
    return clip("\n".join(lines))


def doc_list(rows: list[dict]) -> str:
    if not rows:
        return say("doc_none")
    origin = {"repo": "저장소", "telegram": "업로드"}
    return clip("\n".join([f"📚 문서 {len(rows)}개", *[f"· {r['doc_id']} · {origin.get(r['origin'], r['origin'])} · {r['sections']}절"
                                                     for r in rows], "/docs <이름>으로 절 목록"]))


def doc_key(doc_id: str) -> str:
    return doc_id if len(f"dd:{doc_id}".encode()) <= 64 else "#" + hashlib.sha1(doc_id.encode()).hexdigest()[:12]


def doc_detail(doc_id: str, chunks: list[dict]) -> tuple[str, list]:
    origin = chunks[0].get("origin") if chunks else None
    lines = [f"📄 {doc_id} · {chunks[0].get('title') if chunks else ''}", *[f"#{c['order']} {c['section']}" for c in chunks]]
    return clip("\n".join(lines)), ([] if origin == "repo" else [[("삭제", f"dd:{doc_key(doc_id)}")]])


def doc_draft(op: str, old: dict, text: str, draft_id: str, reason: str, ttl_h: int) -> str:
    head = f"📝 문서 {'삭제' if op == 'delete' else '정정'} 정리본 · {ttl_h}시간 유효"
    lines = [head, f"문서: {old['doc_id']} · #{old['order']} {old['section']}"]
    lines += ["지울 본문:", old["text"][:1500]] if op == "delete" else ["고친 본문:", text[:2500]]
    lines += [f"이유: {reason}", f"정리본 {draft_id}"]
    return clip("\n".join(lines))


def knowledge_saved(op: str, k: dict, indexed: bool = True) -> str:
    note = "" if indexed else "\n검색 반영은 잠시 뒤 다시 시도합니다"
    return f"✅ 지식을 {'삭제' if op == 'delete' else '저장'}했습니다\n{k['title']} · {k['id']} v{k['version']}{note}"


def seed(stats: dict, broken: list[tuple[str, list[str]]], tag: str) -> str:
    lines = [f"📚 지식 시드 적재 · {tag}",
             f"추가 {num(stats.get('inserted'))} · 갱신 {num(stats.get('updated'))} · 색인 실패 {num(stats.get('index_failed'))}"]
    if stats.get("diverged"):
        lines.append("대화로 고친 항목(덮어쓰지 않음): " + ", ".join(stats["diverged"]))
    if stats.get("removed"):
        lines.append("시드에서 사라진 항목(그대로 둠): " + ", ".join(stats["removed"]))
    lines += [f"⚠️ 깨진 항목 {kid}: {'; '.join(errs[:3])}" for kid, errs in broken]
    docs = stats.get("docs") or {}
    if not docs.get("skipped") and docs:
        lines.append(f"📄 저장소 문서 {num(docs.get('docs'))}개 · 새 절 {num(docs.get('added'))} · 삭제 {num(docs.get('removed'))}")
        lines += [f"⚠️ 문서 실패 {x}" for x in docs.get("failed") or []]
    return clip("\n".join(lines))


def seed_failed(error: str) -> str:
    return f"⚠️ 지식 시드를 읽지 못했습니다 ({error})\n기존 지식으로 계속합니다"


def unmatched(rows: list[dict]) -> str:
    lines = ["🧩 미분류 신호 · 최근 24시간", "어떤 지식에도 맞지 않음 — 지식 추가 후보"]
    lines += [f"· {feature(r['feature'])} {step(r['step_id'])} {cause(r.get('reason'), r.get('status'), r.get('error_code'))} {num(r['n'])}건"
              + (f" · {app_code(r['step_id'], r.get('reason'))}" if app_code(r["step_id"], r.get("reason")) else "") for r in rows[:15]]
    return clip("\n".join(lines))


def daily(day: str, data: dict) -> str:
    lines = [f"📊 일일 리포트 · {day}", "", "LLM 호출"]
    for r in data["llm"] or []:
        lines.append(f"· {feature(r['feature'])}: {num(r['calls'])}회 · 실패 {num(r['fails'])} · 재시도 {num(r['retried'])} · "
                     f"토큰 {num(r['input_tokens'])}/{num(r['output_tokens'])} · 평균 {ms(r['avg_ms'])} · 최대 {ms(r['max_ms'])}")
    if not data["llm"]:
        lines.append("· 호출 없음")
    if data["fail_reasons"]:
        lines += ["", "실패 사유 상위"]
        lines += [f"· {feature(r['feature'])} {step(r['step_id'])} {cause(r.get('reason'), r.get('status'))} {num(r['n'])}건"
                  + (f" · {app_code(r['step_id'], r.get('reason'))}" if app_code(r["step_id"], r.get("reason")) else "")
                  for r in data["fail_reasons"][:5]]
    m, h = data["monitor"] or {}, data["health"] or {}
    lines += ["", f"모니터: 실행 {num(m.get('runs'))}회(실패 {num(m.get('failed'))}) · 토큰 {num(m.get('tokens'))} · "
                  f"미발송 {num(h.get('send_fail'))} · 재시작 {num(h.get('restarts'))} · 버린 기록 {num(h.get('dropped'))}"]
    return clip("\n".join(lines))


def policy_signal(subject: str, paragraph: str) -> str:
    key = str(subject or "").removeprefix("drill.").partition("@")[0]
    f, _, s = key.partition(".")
    name = knob(key) if key in KNOBS else f"{feature(f)} · {step(s)}" if f in FEATURES and s else key
    return f"정책 신호 · {name}\n{paragraph}"


def store_unavailable(subject: str) -> str:
    return f"⚠️ 저장소를 쓸 수 없어 진단하지 못했습니다\n{subject}\n감지 결과만 보냅니다. AI DB 상태를 확인하세요"


def store_down(minutes: int) -> str:
    return f"⚠️ AI DB 연결 실패 {minutes}분째\n기록은 컨테이너 버퍼에 보관 중이고 분석은 멈춰 있습니다"


def schema_mismatch(expected: int, actual: int) -> str:
    return f"⚠️ 스키마 버전 불일치 (기대 {expected}, 실제 {actual})\n기록 적재만 하고 분석은 멈춥니다"


def budget_reached(subject: str, count, calls) -> str:
    return f"⚠️ 오늘 모니터 토큰 상한 도달\n진단 없이 감지 결과만 보냅니다: {subject} {count}/{calls}"


def status(ok: bool, info: dict, leader: bool, task: str, error: str | None = None) -> str:
    if not ok:
        return f"⚠️ 상태 확인 실패\nDB 확인 실패: {error}\n리더 {'예' if leader else '아니오'} · 태스크 {task}"
    return "\n".join(["✅ 상태 정상", f"열린 장애 {num(info['incidents'])}건 · 활성 조정 {num(info['flags'])}개 · 지식 {num(info['knowledge'])}개",
                      f"오늘 토큰 {num(info['tokens'])} (훈련 {num(info['drill_tokens'])})", f"리더 {'예' if leader else '아니오'} · 태스크 {task}"])


def flags(rows: list[dict]) -> str:
    if not rows:
        return say("no_flags")
    lines = ["⚙️ 활성 조정"]
    for r in rows:
        until = kst(r["expires_at"].replace(tzinfo=timezone.utc).timestamp()) if r.get("expires_at") else "-"
        lines.append(f"· {knob(r['key'])} = {r['value']} · {until}까지 · {r['set_by']}")
    return clip("\n".join(lines))


def knowledge_list(rows: list[dict]) -> str:
    if not rows:
        return say("no_knowledge")
    return clip("\n".join([f"📚 지식 {len(rows)}개", *[f"· {r['title']} · {r['id']} v{r['version']}" for r in rows]]))


def decided(word: str, who) -> list:
    return [[(f"{word} · {who}"[:40], "x:0")]]


def member_list(rows: list[dict], done: str | None = None) -> tuple[str, list]:
    lines, buttons = [f"👥 가입 요청 {len(rows)}건" if rows else say("member_none")], []
    for r in rows:
        name = r.get("name") or "-"
        at = kst(r["requested_at"].replace(tzinfo=timezone.utc).timestamp(), "%m-%d %H:%M")
        lines.append(f"· {name} @{r.get('username') or '-'} · user {r['user_id']} · chat {r['chat_id']} · {at}")
        buttons.append([(f"승인 · {name}"[:30], f"ma:{r['user_id']}"), ("거절", f"mr:{r['user_id']}")])
    if done:
        lines.append(f"처리: {done}")
    return clip("\n".join(lines)), buttons


def plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))
