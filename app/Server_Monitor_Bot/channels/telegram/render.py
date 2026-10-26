# 파이프라인 메시지 문구: 즉시 알림·요약 알림·일일 리포트·미분류 신호·시드 결과·만료 원복.
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
FEATURE = {"receipt": "영수증", "taxi_meter": "미터기"}
ICON = {"high": "🔴", "medium": "🟠", "low": "🟡", 3: "🔴", 2: "🟠", 1: "🟡"}
REPLY = "답장하면 이 장애로 대화합니다"


def hhmm(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or datetime.now().timestamp(), KST).strftime("%H:%M")


def what(item: dict) -> str:
    sig = item["signal"]
    detail = " ".join(str(v) for v in (sig.get("status"), sig.get("error_code") or sig.get("reason")) if v)
    prefix = "[훈련] " if item.get("drill") else ""
    return f"{prefix}{FEATURE.get(sig['feature'], sig['feature'])} {sig['step_id']} {detail}".strip()


def counts(item: dict) -> str:
    sig = item["signal"]
    return f"{sig['fails']}/{sig['total']}건 (5분)"


def immediate(item: dict, k: dict) -> str:
    checks = " · ".join(k.get("checks") or []) or "-"
    return "\n".join([
        f"{ICON.get(k['severity'], '🟠')} {what(item)} {counts(item)} · {hhmm(item.get('at'))}",
        f"알려진 장애: {k['title']} ({k['id']} v{k['version']})",
        f"원인: {k['cause']}",
        f"해결: {k['resolution']}",
        f"확인: {checks}",
        REPLY,
    ])


def summary(item: dict) -> str:
    held = f" (지난 10분 +{item['held']}건)" if item.get("held") else ""
    return f"{ICON.get(item['rank'], '🟠')} {what(item)} {counts(item)}{held} · {hhmm(item.get('at'))}\n{REPLY}"


def daily(day: str, data: dict) -> str:
    lines = [f"📊 LLM 일일 리포트 {day}"]
    for r in data["llm"] or []:
        name = FEATURE.get(r["feature"], r["feature"])
        lines.append(f" · {name}: 호출 {r['calls']}, 실패 {int(r['fails'] or 0)}, 재시도 {int(r['retried'] or 0)}, "
                     f"토큰 {int(r['input_tokens'] or 0)}/{int(r['output_tokens'] or 0)}, 평균 {int(r['avg_ms'] or 0)}ms, 최대 {int(r['max_ms'] or 0)}ms")
    if not data["llm"]:
        lines.append(" · LLM 호출 없음")
    if data["fail_reasons"]:
        lines.append("실패 사유 상위:")
        lines += [f" · {FEATURE.get(r['feature'], r['feature'])} {r['step_id']} {r['reason'] or '-'} {r['n']}건" for r in data["fail_reasons"][:5]]
    m, h = data["monitor"] or {}, data["health"] or {}
    lines.append(f"모니터: 실행 {int(m.get('runs') or 0)}회(실패 {int(m.get('failed') or 0)}), 토큰 {int(m.get('tokens') or 0)}, "
                 f"미발송 {int(h.get('send_fail') or 0)}, 재시작 {int(h.get('restarts') or 0)}, 버린 기록 {int(h.get('dropped') or 0)}")
    return "\n".join(lines)


def unmatched(rows: list[dict]) -> str:
    lines = ["🧩 미분류 신호(어떤 지식에도 안 맞음, 24시간) — 지식 추가 후보"]
    lines += [f" · {FEATURE.get(r['feature'], r['feature'])} {r['step_id']} reason={r['reason'] or '-'} status={r['status'] or '-'} "
              f"error={r['error_code'] or '-'} {r['n']}건" for r in rows[:15]]
    return "\n".join(lines)


def seed(stats: dict, broken: list[tuple[str, list[str]]], tag: str) -> str:
    lines = [f"📚 지식 시드 적재 ({tag}): 추가 {stats.get('inserted', 0)}, 갱신 {stats.get('updated', 0)}, 색인 실패 {stats.get('index_failed', 0)}"]
    if stats.get("diverged"):
        lines.append("시드와 다름(대화로 고친 항목, 덮어쓰지 않음): " + ", ".join(stats["diverged"]))
    if stats.get("removed"):
        lines.append("시드에서 사라짐(그대로 둠): " + ", ".join(stats["removed"]))
    lines += [f"깨진 항목 {kid}: {'; '.join(errs[:3])}" for kid, errs in broken]
    return "\n".join(lines)


def seed_failed(error: str) -> str:
    return f"📚 지식 시드를 읽지 못했습니다: {error}. 기존 지식으로 계속합니다"


def expired(rows: list[dict]) -> str:
    return "⏱ 조정 만료 원복: " + ", ".join(f"{r['key']}({r['adjustment_id']})" for r in rows)


def store_down(minutes: int) -> str:
    return f"⚠️ AI DB 연결 실패가 {minutes}분 넘게 이어집니다. 기록은 컨테이너 버퍼에 보관 중이고 분석은 멈춰 있습니다"


def schema_mismatch(expected: int, actual: int) -> str:
    return f"⚠️ 스키마 버전 불일치(기대 {expected}, 실제 {actual}). 기록 적재만 하고 분석은 멈춥니다"
