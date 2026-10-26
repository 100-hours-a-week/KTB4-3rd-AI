# M-N3 버튼·명령: 지식 [저장]·[취소], 조정 거절·즉시 원복, 장애 훈련(start·stop·guess), /status·/flags·/knowledge·/help.
import json
import math
import re
import threading
import time
import uuid
from pathlib import Path

import httpx

from ..runtime.context import new_event
from ..store.repos.base import epoch
from .common.env import Env
from .diagnose import prompts as diagnose_prompts
from .state import message

DRILLS = Path(__file__).resolve().parents[1] / "drills"
SCENARIO_ID = re.compile(r"^S-[0-9a-f]{4}$")
CONFIGS = ("single", "rag", "agent")
HELP = """명령
/status 상태 · /flags 활성 플래그 · /knowledge [검색어] 지식 목록
/trace <request_id> 요청 추적
/drill start <id> · /drill stop · /drill guess <원인 설명> (dev·local)
그 밖의 메시지는 챗봇이 답합니다. 알림·리포트에 답장하면 그 장애로 대화합니다.
지식 추가·수정·삭제는 대화로 요청하면 정리본과 [저장][취소]가 옵니다."""


def load_scenario(scenario_id: str) -> dict | None:
    if not SCENARIO_ID.match(scenario_id or ""):
        return None
    path = DRILLS / f"{scenario_id}.json"
    return json.loads(path.read_text()) if path.is_file() else None


def send_traffic(base_url: str, feature: str, image_url: str, run_id: str, count: int = 20, interval: float = 15.0) -> None:
    def run():
        for i in range(count):
            try:
                httpx.post(f"{base_url}/{feature}", json={"image_URL": image_url},
                           headers={"X-Request-ID": f"drill-{run_id[-4:]}-{i:02d}-{uuid.uuid4().hex[:6]}"}, timeout=70)
            except httpx.HTTPError:
                pass
            time.sleep(interval)
    threading.Thread(target=run, name="server-bot-drill", daemon=True).start()


class Actions:
    def __init__(self, env: Env, traffic=send_traffic):
        self.env, self.traffic = env, traffic

    @property
    def repos(self):
        return self.env.ctx.repos

    def handle(self, event: dict) -> dict:
        name = event["type"].removeprefix("action.")
        p = event.get("payload") or {}
        if p.get("callback_id") and self.env.ctx.telegram:
            try:
                self.env.ctx.telegram.answer_callback(p["callback_id"], "처리 중")
            except Exception:
                pass
        out = getattr(self, name)(event, p)
        msgs = [m if m.get("chat_id") else {**m, "chat_id": event.get("chat_id")} for m in out.get("outbox", [])]
        return {**out, "outbox": msgs}

    def reply(self, event, text: str, **kw) -> dict:
        return {"outbox": [message(text, chat_id=event.get("chat_id"), reply_to=(event.get("payload") or {}).get("message_id"), **kw)]}

    def knowledge_save(self, event, p):
        res = self.repos.knowledge.apply_draft(p.get("draft_id"), event.get("from_id"))
        return self.reply(event, res["message"])

    def knowledge_cancel(self, event, p):
        ok = self.repos.knowledge.cancel_draft(p.get("draft_id"), event.get("from_id"))
        return self.reply(event, "정리본을 취소했습니다" if ok else "이미 처리했거나 없는 정리본입니다")

    def adapt_reject(self, event, p):
        adj = self.repos.adjust.get(p.get("adj_id"))
        if not adj or adj["status"] != "proposed":
            return self.reply(event, "이미 처리했거나 없는 조정입니다")
        self.repos.adjust.set_status(adj["id"], "rejected")
        return self.reply(event, f"조정을 거절했습니다: {adj['knob']}")

    def adapt_rollback(self, event, p):
        adj = self.repos.adjust.get(p.get("adj_id"))
        if not adj or adj["status"] not in ("applied", "kept"):
            return self.reply(event, "되돌릴 활성 조정이 없습니다")
        self.repos.adjust.rollback(adj["id"], adj["knob"])
        return self.reply(event, f"원래 값으로 되돌렸습니다: {adj['knob']}")

    def drill_start(self, event, p):
        scenario = load_scenario(p.get("arg", "").split()[0] if p.get("arg") else "")
        if not scenario:
            return self.reply(event, "시나리오를 찾지 못했습니다. 예: /drill start S-7a1c")
        if self.repos.drills.current():
            return self.reply(event, "이미 진행 중인 훈련이 있습니다. /drill stop 뒤에 시작하세요")
        if not str(scenario.get("image_url", "")).startswith("http"):
            return self.reply(event, f"{scenario['id']}에 image_url(개인정보 없는 dev 테스트 이미지)을 먼저 넣어 주세요")
        run_id = self.repos.drills.start(scenario["id"], scenario["group"], event.get("from_id") or 0)
        inject = scenario["inject"]
        self.repos.adjust.set_flag(f"drill.{inject['point']}", inject["behavior"], time.time() + min(20 * 15 + 60, 600), "drill", run_id)
        base = self.env.ctx.settings.section("drill").get("base_url", "http://127.0.0.1:8000")
        self.traffic(base, scenario["feature"], scenario["image_url"], run_id)
        return self.reply(event, f"훈련 시작: {run_id} (합성 요청 20건, 15초 간격). 끝나면 /drill stop")

    def drill_stop(self, event, p):
        run = self.repos.drills.current()
        if not run:
            return self.reply(event, "진행 중인 훈련이 없습니다")
        scenario = load_scenario(run["scenario_id"]) or {}
        inject = scenario.get("inject") or {}
        if inject.get("point"):
            self.repos.adjust.delete_flag(f"drill.{inject['point']}")
        self.repos.drills.stop(run["id"])
        now = time.time()
        minutes = min(60, math.ceil((now - epoch(run["started_at"])) / 60) + 1)
        expected = scenario.get("expected") or {}
        if run["group"] == "C":
            fp = self.repos.drills.incidents_since(run["started_at"]) > 0
            self.repos.drills.add_eval({"drill_run_id": run["id"], "scenario_id": run["scenario_id"], "group": "C", "config": "detector",
                                        "run_no": 1, "expected_step_id": expected.get("step_id"), "false_positive": fp,
                                        "duration_ms": int((now - epoch(run["started_at"])) * 1000), "tokens": 0})
            return self.reply(event, f"훈련 종료: {run['id']} (묶음 C, 채점 1행)")
        feature, step, reason = scenario.get("feature"), expected.get("step_id"), expected.get("reason")
        samples = self.repos.metrics.samples(feature, step, reason, drill=True, seconds=minutes * 60)
        followups = []
        for i, (config, run_no) in enumerate((c, n) for c in CONFIGS for n in (1, 2, 3)):
            payload = {"alert_key": f"drill.{feature}.{step}.{reason}", "feature": feature, "step_id": step, "reason": reason,
                       "summary": f"[훈련] {feature} {step} 실패", "sample_ids": samples, "detected_at": now,
                       "signal": {"feature": feature, "step_id": step, "reason": reason},
                       "drill": {"run_id": run["id"], "scenario_id": run["scenario_id"], "group": run["group"], "expected": expected,
                                 "config": config, "run_no": run_no, "hide_knowledge": scenario.get("hide_knowledge") or [],
                                 "minutes": minutes}}
            followups.append({"event": new_event("alert.pipeline", payload, source="followup", chain_depth=1), "not_before": now + 60 * (i + 1)})
        return {**self.reply(event, f"훈련 종료: {run['id']}. 비교 실행 9건을 60초 간격으로 예약했습니다"), "followups": followups}

    def drill_guess(self, event, p):
        run = self.repos.drills.current()
        text = (p.get("arg") or "").strip()
        if not run or not text:
            return self.reply(event, "진행 중인 훈련이 없거나 원인 설명이 비었습니다. 예: /drill guess OpenAI 한도 초과")
        if run.get("human_guess_1") and run.get("human_guess_2"):
            return self.reply(event, "추정은 두 번까지 기록합니다")
        n = 2 if run.get("human_guess_1") else 1
        expected = (load_scenario(run["scenario_id"]) or {}).get("expected") or {}
        hit, reason = self.env.judge(diagnose_prompts.JUDGE, expected.get("cause_text", ""), text)
        self.repos.drills.guess(run["id"], n, text, int((time.time() - epoch(run["started_at"])) * 1000), hit, reason)
        return self.reply(event, f"{n}회차 추정을 기록했습니다")

    def status(self, event, p):
        r, ok = self.repos, True
        try:
            r.db.ping()
            flags = r.adjust.flags()
            lines = [f"DB 정상 · 열린 장애 {r.incidents.open_count()}건 · 활성 플래그 {len(flags)}개",
                     f"오늘 모니터 토큰 {r.coord.tokens_today(False):,} (훈련 {r.coord.tokens_today(True):,})",
                     f"지식 {len(r.knowledge.titles(limit=1000))}개"]
        except Exception as exc:
            ok, lines = False, [f"DB 확인 실패: {type(exc).__name__}"]
        lines.append(f"리더 {'예' if self.env.ctx.is_leader() else '아니오'} · 태스크 {self.env.ctx.task_id[:6]}")
        return self.reply(event, ("✅ " if ok else "⚠️ ") + "\n".join(lines))

    def flags(self, event, p):
        rows = self.repos.adjust.flags()
        return self.reply(event, "\n".join(f"{r['key']} = {r['value']} (만료 {r['expires_at'] or '-'}, {r['set_by']})" for r in rows)
                          or "활성 플래그 없음")

    def knowledge_list(self, event, p):
        rows = self.repos.knowledge.titles(p.get("query") or None)
        return self.reply(event, "\n".join(f"{r['id']} v{r['version']} {r['title']}" for r in rows) or "지식이 없습니다")

    def help(self, event, p):
        return self.reply(event, HELP)
