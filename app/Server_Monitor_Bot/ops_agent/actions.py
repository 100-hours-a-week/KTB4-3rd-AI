# M-N3 버튼·명령: 지식·문서 [저장]·[취소], 조정 거절·즉시 원복, 장애 훈련(start·stop·guess), 팀원 가입(/join·/request·승인·거절), 문서 올리기·/docs·삭제, /status·/flags·/knowledge·/help.
import json
import math
import os
import re
import threading
import time
import uuid
from pathlib import Path

import httpx

from ..channels.telegram import render
from ..docs.models import Source
from ..docs.pipeline import ingest
from ..runtime.context import new_event
from ..store.repos.base import epoch
from .common.env import Env
from .diagnose import prompts as diagnose_prompts
from .state import marks, message

DRILLS = Path(__file__).resolve().parents[1] / "drills"
SCENARIO_ID = re.compile(r"^S-[0-9a-f]{4}$")
CONFIGS = ("single", "rag", "agent")
HELP = render.HELP


def load_scenario(scenario_id: str) -> dict | None:
    if not SCENARIO_ID.match(scenario_id or ""):
        return None
    path = DRILLS / f"{scenario_id}.json"
    return json.loads(path.read_text()) if path.is_file() else None


def send_traffic(base_url: str, feature: str, image_url: str, run_id: str, count: int = 20, interval: float = 15.0) -> None:
    def run():
        for i in range(count):
            time.sleep(interval)
            try:
                httpx.post(f"{base_url}/{feature}", json={"image_URL": image_url},
                           headers={"X-Request-ID": f"drill-{run_id[-4:]}-{i:02d}-{uuid.uuid4().hex[:6]}"}, timeout=70)
            except httpx.HTTPError:
                pass
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
                self.env.ctx.telegram.answer_callback(p["callback_id"], render.say("processing"))
            except Exception:
                pass
        out = getattr(self, name)(event, p)
        msgs = [m if m.get("chat_id") else {**m, "chat_id": event.get("chat_id")} for m in out.get("outbox", [])]
        return {**out, "outbox": msgs}

    def reply(self, event, text: str, **kw) -> dict:
        return {"outbox": [message(text, chat_id=event.get("chat_id"), reply_to=(event.get("payload") or {}).get("message_id"), **kw)]}

    def decided(self, out: dict, kind: str, ref_id: str, word: str, p: dict) -> dict:
        return {**out, "outbox": [*marks(self.repos.coord, kind, ref_id, render.decided(word, p.get("name") or "-")), *out["outbox"]]}

    def knowledge_save(self, event, p):
        draft = self.repos.knowledge.get_draft(p.get("draft_id")) or {}
        if draft.get("kind") == "doc" and not self.env.ctx.settings.is_admin(event.get("from_id")):
            return self.reply(event, render.say("denied"))
        res = self.repos.knowledge.apply_draft(p.get("draft_id"), event.get("from_id"))
        if res.get("ok") and res.get("doc"):
            return self.decided(self.reply(event, f"✅ {res['message']}"), "draft", p.get("draft_id"), "✅ 저장", p)
        if res.get("ok") and res.get("knowledge"):
            return self.decided(self.reply(event, render.knowledge_saved(res.get("op", "add"), res["knowledge"], res.get("indexed", True))),
                                "draft", p.get("draft_id"), "✅ 저장", p)
        return self.reply(event, res["message"] if res.get("ok") else render.say("failed", reason=res["message"]))

    def knowledge_cancel(self, event, p):
        if not self.repos.knowledge.cancel_draft(p.get("draft_id"), event.get("from_id")):
            return self.reply(event, render.say("draft_gone"))
        return self.decided(self.reply(event, render.say("draft_cancelled")), "draft", p.get("draft_id"), "✖ 취소", p)

    def adapt_reject(self, event, p):
        adj = self.repos.adjust.get(p.get("adj_id"))
        if not adj or not self.repos.adjust.set_status(adj["id"], "rejected", expect=("proposed",)):
            return self.reply(event, render.say("adjust_gone", adj_id=p.get("adj_id")))
        return self.decided(self.reply(event, render.say("adjust_rejected", knob=render.knob(adj["knob"]))), "adjustment", adj["id"], "✖ 거절", p)

    def adapt_rollback(self, event, p):
        adj = self.repos.adjust.get(p.get("adj_id"))
        if not adj or not self.repos.adjust.rollback(adj["id"], adj["knob"]):
            return self.reply(event, render.say("adjust_none"))
        return self.reply(event, render.say("adjust_rolled_back", knob=render.knob(adj["knob"])))

    def drill_start(self, event, p):
        scenario = load_scenario(p.get("arg", "").split()[0] if p.get("arg") else "")
        if not scenario:
            return self.reply(event, render.say("drill_not_found"))
        if self.repos.drills.current():
            return self.reply(event, render.say("drill_running"))
        image_url = scenario.get("image_url") or os.environ.get("DRILL_IMAGE_URL", "")
        if not image_url.startswith("http"):
            return self.reply(event, render.say("drill_no_image", scenario=scenario["id"]))
        run_id = self.repos.drills.start(scenario["id"], scenario["group"], event.get("from_id") or 0)
        inject = scenario["inject"]
        self.repos.adjust.set_flag(f"drill.{inject['point']}", inject["behavior"], time.time() + min(20 * 15 + 60, 600), "drill", run_id)
        base = self.env.ctx.settings.section("drill").get("base_url", "http://127.0.0.1:8000")
        self.traffic(base, scenario["feature"], image_url, run_id)
        return self.reply(event, render.say("drill_started", run_id=run_id))

    def drill_stop(self, event, p):
        run = self.repos.drills.current()
        if not run:
            return self.reply(event, render.say("drill_none"))
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
            return self.reply(event, render.say("drill_stopped_c", run_id=run["id"]))
        feature, step, reason = scenario.get("feature"), expected.get("step_id"), expected.get("reason")
        samples = self.repos.metrics.samples(feature, step, reason, drill=True, seconds=minutes * 60)
        followups = []
        for i, (config, run_no) in enumerate((c, n) for c in CONFIGS for n in (1, 2, 3)):
            payload = {"alert_key": f"drill.{feature}.{step}.{reason}", "feature": feature, "step_id": step, "reason": reason,
                       "summary": f"[훈련] {feature} {step} 실패", "sample_ids": samples, "detected_at": now + 60 * (i + 1),
                       "signal": {"feature": feature, "step_id": step, "reason": reason},
                       "drill": {"run_id": run["id"], "scenario_id": run["scenario_id"], "group": run["group"], "expected": expected,
                                 "config": config, "run_no": run_no, "hide_knowledge": scenario.get("hide_knowledge") or [],
                                 "minutes": minutes}}
            followups.append({"event": new_event("alert.pipeline", payload, source="followup", chain_depth=1), "not_before": now + 60 * (i + 1)})
        return {**self.reply(event, render.say("drill_stopped", run_id=run["id"])), "followups": followups}

    def drill_guess(self, event, p):
        run = self.repos.drills.current()
        text = (p.get("arg") or "").strip()
        if not run or not text:
            return self.reply(event, render.say("drill_guess_empty"))
        if run.get("human_guess_1") and run.get("human_guess_2"):
            return self.reply(event, render.say("drill_guess_full"))
        n = 2 if run.get("human_guess_1") else 1
        expected = (load_scenario(run["scenario_id"]) or {}).get("expected") or {}
        hit, reason = self.env.judge(diagnose_prompts.JUDGE, expected.get("cause_text", ""), text)
        self.repos.drills.guess(run["id"], n, text, int((time.time() - epoch(run["started_at"])) * 1000), hit, reason)
        return self.reply(event, render.say("drill_guessed", n=n))

    def status(self, event, p):
        r, leader, task = self.repos, self.env.ctx.is_leader(), self.env.ctx.task_id[:6]
        try:
            r.db.ping()
            info = {"incidents": r.incidents.open_count(), "flags": len(r.adjust.flags()), "knowledge": len(r.knowledge.titles(limit=1000)),
                    "tokens": r.coord.tokens_today(False), "drill_tokens": r.coord.tokens_today(True)}
        except Exception as exc:
            return self.reply(event, render.status(False, {}, leader, task, type(exc).__name__))
        return self.reply(event, render.status(True, info, leader, task))

    def flags(self, event, p):
        return self.reply(event, render.flags(self.repos.adjust.flags()))

    def knowledge_list(self, event, p):
        return self.reply(event, render.knowledge_list(self.repos.knowledge.titles(p.get("query") or None)))

    def help(self, event, p):
        return self.reply(event, HELP)

    def member_join(self, event, p):
        uid = event.get("from_id")
        settings = self.env.ctx.settings
        if settings.is_admin(uid) or (event.get("chat_id") is not None and event.get("chat_id") == settings.discord_channel_id):
            return self.reply(event, render.say("member_exists"))
        state = self.repos.members.request(uid, event.get("chat_id"), p.get("name"), p.get("username"))
        return self.reply(event, render.say({"new": "member_requested", "pending": "member_pending"}.get(state, "member_exists")))

    def doc_upload(self, event, p):
        cfg, tg = self.env.ctx.settings.section("docs"), self.env.ctx.telegram
        if (p.get("file_size") or 0) > cfg.get("max_mb", 20) * 1024 * 1024:
            return self.reply(event, render.say("doc_failed", reason=f"{cfg.get('max_mb', 20)}MB 초과"))
        data = tg.fetch_file(p.get("file_id"), cfg.get("max_mb", 20) * 1024 * 1024) if tg else None
        if not data:
            return self.reply(event, render.say("doc_failed", reason="파일을 받지 못했습니다"))
        try:
            result = ingest(Source(p.get("file_name") or "doc.txt", data, "telegram", event.get("from_id")), self.repos.docs, **cfg)
        except ValueError as exc:
            return self.reply(event, render.say("doc_failed", reason=str(exc)))
        except Exception as exc:
            return self.reply(event, render.say("doc_failed", reason=type(exc).__name__))
        return self.reply(event, render.doc_saved(result))

    def doc_list(self, event, p):
        name = (p.get("query") or "").strip()
        if not name:
            return self.reply(event, render.doc_list(self.repos.docs.docs()))
        chunks = self.repos.docs.chunks_of(name)
        if not chunks:
            return self.reply(event, render.say("doc_missing", doc_id=name))
        text, buttons = render.doc_detail(name, chunks)
        return self.reply(event, text, buttons=buttons, ref=("doc", name))

    def doc_delete(self, event, p):
        doc_id = p.get("doc_id") or ""
        if doc_id.startswith("#"):
            doc_id = next((d["doc_id"] for d in self.repos.docs.docs() if render.doc_key(d["doc_id"]) == doc_id), doc_id)
        chunks = self.repos.docs.chunks_of(doc_id)
        if not chunks:
            return self.reply(event, render.say("doc_missing", doc_id=doc_id))
        if any(c.get("origin") == "repo" for c in chunks):
            return self.reply(event, render.say("doc_repo", doc_id=doc_id))
        n = self.repos.docs.delete_doc(doc_id)
        return self.decided(self.reply(event, render.say("doc_deleted", doc_id=doc_id, n=n)), "doc", doc_id, "🗑 삭제", p)

    def member_list(self, event, p):
        rows = self.repos.members.pending()
        if not rows:
            return self.reply(event, render.say("member_none"))
        text, buttons = render.member_list(rows)
        return self.reply(event, text, buttons=buttons, ref=("member", "list"))

    def member_decide(self, event, p, approve: bool):
        try:
            uid = int(p.get("user_id"))
        except (TypeError, ValueError):
            return self.reply(event, render.say("member_bad"))
        row = self.repos.members.get(uid)
        done = row and row["status"] == "pending" and (self.repos.members.approve(uid, event.get("from_id")) if approve
                                                         else self.repos.members.reject(uid))
        if not done:
            return self.reply(event, render.say("member_done"))
        name = row["name"] or uid
        text, buttons = render.member_list(self.repos.members.pending(), f"{name} {'승인' if approve else '거절'} · {p.get('name') or '-'}")
        return {"outbox": [*marks(self.repos.coord, "member", "list", buttons, text, hours=24),
                           *self.reply(event, render.say("member_approved" if approve else "member_rejected", name=name))["outbox"],
                           message(render.say("member_welcome" if approve else "member_denied"), chat_id=row["chat_id"])]}

    def member_approve(self, event, p):
        return self.member_decide(event, p, True)

    def member_reject(self, event, p):
        return self.member_decide(event, p, False)
