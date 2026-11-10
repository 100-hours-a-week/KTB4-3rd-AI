# 서버 장애 모니터링 봇 벤치마크(v1·v2 비교용): 기능 묶음별 단계 지연 분포·토큰·판단 모델 정확도·대기열을 봇 컨테이너 안에서 재서 JSON으로 남긴다. .venv/bin/python tools/server_monitor/bench.py run [묶음…] | report <원자료.json> [비교.json]
import collections
import functools
import itertools
import json
import os
import random
import re
import statistics
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve()
BOX = "/tmp/bench"
RAW = f"{BOX}/raw.json"
RAW_IN = f"{BOX}/raw_in.json"
CONTAINER = os.environ.get("BENCH_CONTAINER", "sbm-app")
DOCS = Path(os.environ.get("BENCH_DOCS", HERE.parent / "samples"))
FILES = {"md": "T1_택시미터_장애대응.md", "txt": "T2_영수증_운영메모.txt", "docx": "T3_혼잡도_점검표.docx", "pdf": "T4_배포_체크리스트.pdf"}
GROUPS = ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9")
APP = "http://127.0.0.1:8000"
IMG = "http://127.0.0.1:8799"
OK_IMAGE = HERE.parent / "samples" / "ok.png"
DECISIONS = HERE.parent / "samples" / "decisions.json"
SCALE = float(os.environ.get("BENCH_SCALE", "1"))
CATS = ("llm", "embed", "qdrant", "mysql", "telegram", "app", "convert")

SAMPLES: dict[str, list] = collections.defaultdict(list)
MESSAGES: dict[str, list] = collections.defaultdict(list)
LLM_CALLS: list[dict] = []
EXTRA: dict = {}
STATE = {"cur": None, "key": None, "nodes": [], "last": {"text": "", "buttons": []}}


def n(x: int) -> int:
    return max(1, round(x * SCALE))


def log(text: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {text}"
    print(line, flush=True)
    with open(f"{BOX}/progress.log", "a") as f:
        f.write(line + "\n")


def add(cat: str, ms: float) -> None:
    cur = STATE["cur"]
    if cur is not None:
        cur["cats"][cat] += ms


def step(label: str, ms: float) -> None:
    cur = STATE["cur"]
    if cur is not None:
        cur["steps"][label] += ms


def value(key: str, ms: float, ok: bool = True, **extra) -> None:
    SAMPLES[key].append({"ms": round(ms, 1), "ok": ok, "steps": {}, "cats": {}, "tin": 0, "tout": 0, "llm": 0, "extra": extra})


def sample(key: str, fn):
    cur = {"steps": collections.defaultdict(float), "cats": collections.defaultdict(float), "tin": 0, "tout": 0, "llm": 0, "extra": {}}
    STATE["cur"], STATE["key"] = cur, key
    ok, err, res = True, None, None
    t = time.perf_counter()
    try:
        res = fn()
        if isinstance(res, dict) and res.get("outcome") in ("error", "dropped"):
            ok, err = False, res.get("error") or res.get("drop_reason") or res.get("outcome")
    except Exception as exc:
        ok, err = False, f"{type(exc).__name__}: {str(exc)[:120]}"
    ms = (time.perf_counter() - t) * 1000
    STATE["cur"] = None
    SAMPLES[key].append({"ms": round(ms, 1), "ok": ok, "err": err, "steps": {k: round(v, 1) for k, v in cur["steps"].items()},
                         "cats": {k: round(v, 1) for k, v in cur["cats"].items()}, "tin": cur["tin"], "tout": cur["tout"],
                         "llm": cur["llm"], "extra": cur["extra"]})
    if not ok:
        log(f"  실패 {key}: {err}")
    return res


def timed(owner, name: str, label: str, cat: str | None = None):
    fn = getattr(owner, name)

    @functools.wraps(fn)
    def wrapper(*a, **k):
        STATE["nodes"].append(label)
        t = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            ms = (time.perf_counter() - t) * 1000
            STATE["nodes"].pop()
            step(label, ms)
            if cat:
                add(cat, ms)
    setattr(owner, name, wrapper)


def serve_images() -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    body = Path(f"{BOX}/ok.png").read_bytes()

    class Images(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/ok"):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/slow"):
                time.sleep(8)
            self.send_response(500 if self.path.startswith("/broken") else 404)
            self.end_headers()

        def log_message(self, *args):
            pass
    threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", 8799), Images).serve_forever, daemon=True).start()
    os.environ["DRILL_IMAGE_URL"] = f"{IMG}/ok.png"


def instrument() -> None:
    import httpx

    from app.Server_Monitor_Bot.docs import convert, pipeline
    from app.Server_Monitor_Bot.ops_agent import actions, events, main_graph
    from app.Server_Monitor_Bot.ops_agent.adapt import nodes as adapt
    from app.Server_Monitor_Bot.ops_agent.chat import nodes as chat
    from app.Server_Monitor_Bot.ops_agent.common import decide
    from app.Server_Monitor_Bot.ops_agent.diagnose import nodes as diag
    from app.Server_Monitor_Bot.ops_agent.knowledge import nodes as know
    from app.Server_Monitor_Bot.store.mysql import MySQLDB

    def patch_client(client_cls) -> None:
        send = client_cls.send

        def patched(self, request, *a, **k):
            t = time.perf_counter()
            res = None
            try:
                res = send(self, request, *a, **k)
                return res
            finally:
                ms = (time.perf_counter() - t) * 1000
                host, path = request.url.host or "", request.url.path or ""
                cat = ("embed" if "embeddings" in path else "llm") if "openai" in host else "telegram" if "telegram" in host else \
                    "qdrant" if request.url.port == 6333 else "app" if request.url.port == 8000 else "http"
                add(cat, ms)
                if cat == "llm":
                    usage = {}
                    try:
                        usage = json.loads(res.content).get("usage") or {} if res is not None else {}
                    except Exception:
                        pass
                    tin, tout = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
                    cur = STATE["cur"]
                    if cur is not None:
                        cur["tin"] += tin
                        cur["tout"] += tout
                        cur["llm"] += 1
                    LLM_CALLS.append({"key": STATE["key"], "node": STATE["nodes"][-1] if STATE["nodes"] else "-", "ms": round(ms, 1),
                                      "in": tin, "out": tout, "status": getattr(res, "status_code", None)})
        client_cls.send = patched
    patch_client(httpx.Client)
    try:
        import httpx2

        patch_client(httpx2.Client)
    except ImportError:
        pass
    for name in ("execute", "insert", "insert_many", "fetch_one", "fetch_all"):
        fn = getattr(MySQLDB, name)

        def wrapper(*a, _fn=fn, **k):
            t = time.perf_counter()
            try:
                return _fn(*a, **k)
            finally:
                add("mysql", (time.perf_counter() - t) * 1000)
        setattr(MySQLDB, name, wrapper)
    for name in ("ingest", "route", "diagnose", "chat", "chain", "knowledge", "propose", "apply", "action", "publish"):
        timed(main_graph.Nodes, name, f"M.{name}")
    timed(decide.Decider, "route", "J.route")
    timed(decide.Decider, "same_cause", "J.same_cause")
    for cls, label, names in ((chat.Chat, "S2", ("prepare", "fast_edit", "investigate", "answer", "save")),
                              (diag.Diagnose, "S1", ("context", "investigate", "report", "persist")),
                              (know.KnowledgeEdit, "S3", ("collect", "draft", "confirm")),
                              (adapt.Propose, "S4a", ("match", "propose", "request")),
                              (adapt.Apply, "S4b", ("apply", "observe", "conclude", "report"))):
        for name in names:
            timed(cls, name, f"{label}.{name}")
    timed(chat, "context_of", "S2.history")
    for mod in (chat, diag, know, adapt):
        for name, label in (("run_investigate", "W1"), ("run_write", "W2")):
            if not hasattr(mod, name):
                continue
            fn = getattr(mod, name)

            def wrapper(*a, _fn=fn, _label=label, **k):
                out = _fn(*a, **k)
                cur = STATE["cur"]
                if cur is not None:
                    ex = cur["extra"]
                    if _label == "W1":
                        ex["w1_rounds"] = ex.get("w1_rounds", 0) + out.get("rounds", 0)
                        ex["w1_stop"] = out.get("stop_reason")
                    else:
                        ex["w2_retries"] = ex.get("w2_retries", 0) + out.get("retries", 0)
                        ex["w2_valid"] = out.get("valid")
                        ex.setdefault("w2_errors", []).extend(str(e)[:120] for e in (out.get("errors") or [])[:3])
                return out
            timed_fn = functools.wraps(fn)(wrapper)
            setattr(mod, name, timed_fn)
            timed(mod, name, label)
    for name in [t.removeprefix("action.") for t in events.ACTIONS]:
        if hasattr(actions.Actions, name):
            timed(actions.Actions, name, f"A.{name}")
    for name in ("convert", "split", "clean", "dedupe", "apply"):
        timed(pipeline, name, f"D.{name}")
    timed(convert.Converter, "rich", "D.rich", "convert")


def inner(groups: list[str]) -> None:
    import httpx

    os.makedirs(BOX, exist_ok=True)
    serve_images()
    instrument()
    from app.Server_Monitor_Bot.channels.telegram.client import TelegramClient
    from app.Server_Monitor_Bot.channels.telegram.receiver import to_event
    from app.Server_Monitor_Bot.config.settings import load
    from app.Server_Monitor_Bot.docs.convert import doc_id
    from app.Server_Monitor_Bot.hooks.mask import mask
    from app.Server_Monitor_Bot.ops_agent.diagnose import prompts as diag_prompts
    from app.Server_Monitor_Bot.ops_agent.main_graph import run_event, runtime
    from app.Server_Monitor_Bot.ops_agent.roles import role_of
    from app.Server_Monitor_Bot.pipelines import report, unmatched
    from app.Server_Monitor_Bot.runtime.context import Ctx, new_event
    from app.Server_Monitor_Bot.runtime.runner import vector
    from app.Server_Monitor_Bot.runtime.scheduler import Scheduler
    from app.Server_Monitor_Bot.store.mysql import MySQLDB
    from app.Server_Monitor_Bot.store.repos import Repos

    s = load()
    s.cfg["budget"]["daily_tokens"] = s.cfg["budget"]["drill_daily_tokens"] = 10 ** 10
    db = MySQLDB.from_url(s.db_url, max_connections=4)
    vec, embed = vector(s)
    repos = Repos(db, vec, embed, tuple(s.policy))
    admin = s.admin_ids[0]
    real = TelegramClient(os.environ.get("BENCH_TG_TOKEN"), s.admin_ids, f"[{s.stage.upper()}]")
    null = TelegramClient(None, s.admin_ids, f"[{s.stage.upper()}]")
    timed(real, "fetch_file", "D.download")
    null.fetch_file = real.fetch_file

    def capture(method, payload, timeout=10, retry=True):
        if method in ("sendMessage", "editMessageText"):
            text = mask(str(payload.get("text") or ""))
            buttons = [b["callback_data"] for row in (payload.get("reply_markup") or {}).get("inline_keyboard", []) for b in row]
            STATE["last"] = {"text": text, "buttons": buttons}
            if STATE["key"] and len(MESSAGES[STATE["key"]]) < 3:
                MESSAGES[STATE["key"]].append({"text": text, "buttons": buttons, "html": payload.get("parse_mode") == "HTML"})
        return None
    null.call = capture
    follow: list = []
    ctx = Ctx(settings=s, repos=repos, telegram=null, task_id="bench", image_tag="bench",
              enqueue=lambda event, not_before=None: follow.append((event, not_before)), is_leader=lambda: True)
    nodes, _ = runtime(ctx)
    role = lambda uid: role_of(uid, s, repos.members)
    seed = int(time.time() * 1000)
    chats, mids = itertools.count(seed), itertools.count(seed % 10 ** 9)

    def update(chat: int, **msg) -> dict:
        mid = next(mids)
        return {"update_id": mid, "message": {"message_id": mid, "from": {"id": admin, "first_name": "관리자"}, "chat": {"id": chat},
                                              "date": int(time.time()), **msg}}

    def parsed(upd: dict) -> dict:
        t = time.perf_counter()
        ev = to_event(upd, role, repos.coord.ref_of)
        step("M.receive_parse", (time.perf_counter() - t) * 1000)
        if ev is None:
            raise RuntimeError("to_event None")
        return ev

    def text(t: str, chat: int | None = None) -> dict:
        return run_event(ctx, parsed(update(chat or next(chats), text=t)))

    def reply(t: str, ref: str, chat: int | None = None) -> dict:
        ev = new_event("user.message", {"message_id": next(mids), "text": t, "reply_to_message_id": 1, "reply_ref": ref},
                       source="telegram", from_id=admin, chat_id=chat or next(chats))
        return run_event(ctx, ev)

    def button(data: str) -> dict:
        mid = next(mids)
        upd = {"update_id": mid, "callback_query": {"id": f"b{mid}", "from": {"id": admin, "first_name": "관리자"}, "data": data,
                                                    "message": {"message_id": 1, "chat": {"id": next(chats)}}}}
        return run_event(ctx, parsed(upd))

    def document(file_id: str, name: str) -> dict:
        return run_event(ctx, parsed(update(next(chats), document={"file_id": file_id, "file_name": name, "file_size": 1000})))

    def expect(prefix: str) -> str:
        found = [b for b in STATE["last"]["buttons"] if b.startswith(prefix)]
        if not found:
            rows = SAMPLES.get(STATE["key"] or "")
            if rows:
                rows[-1]["extra"]["goal_fail"] = f"버튼 없음({prefix})"
            raise RuntimeError(f"버튼 없음({prefix}) · {STATE['last']['text'][:80]}")
        return found[0].split(":", 1)[1]

    def traffic(kind: str, count: int = 20) -> float:
        def one(i):
            httpx.post(f"{APP}/receipt", json={"image_URL": f"{IMG}/{kind}.png"},
                       headers={"X-Request-ID": f"bench-{kind}-{i:02d}-{uuid.uuid4().hex[:6]}"}, timeout=70)
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(one, range(count)))
        return time.time()

    def hold_alert(key: str) -> None:
        db.execute("INSERT INTO alerts (alert_key, last_at, `rank`, `count`, task_id) VALUES (%s, NOW(3), 3, 1000000, 'bench') AS new "
                   "ON DUPLICATE KEY UPDATE last_at = NOW(3), `rank` = 3, `count` = 1000000", (key,))

    def wait_for(sql: str, params=(), timeout: float = 180) -> dict | None:
        end = time.time() + timeout
        while time.time() < end:
            row = db.fetch_one(sql, params)
            if row and any(v is not None for v in row.values()):
                return row
            time.sleep(0.1)
        return None

    def ids(like: str, limit: int) -> list[str]:
        return [r["request_id"] for r in db.fetch_all("SELECT request_id FROM request_results WHERE request_id LIKE %s ORDER BY ts DESC LIMIT %s",
                                                      (like, limit))]

    def g0():
        def one(i):
            rid = f"bench-rec-{i:03d}-{uuid.uuid4().hex[:6]}"
            t = time.perf_counter()
            httpx.post(f"{APP}/receipt", json={"image_URL": f"{IMG}/missing.png"}, headers={"X-Request-ID": rid}, timeout=30)
            return rid, time.time(), (time.perf_counter() - t) * 1000
        for b in range(n(10)):
            with ThreadPoolExecutor(10) as pool:
                done = list(pool.map(one, range(b * 10, b * 10 + 10)))
            pending = {rid: (at, ms) for rid, at, ms in done}
            end = time.time() + 30
            while pending and time.time() < end:
                marks = ", ".join(["%s"] * len(pending))
                for r in db.fetch_all(f"SELECT request_id FROM request_results WHERE request_id IN ({marks})", tuple(pending)):
                    at, ms = pending.pop(r["request_id"])
                    value("0_공통/요청 → MySQL 기록", (time.time() - at) * 1000)
                    value("0_공통/앱 요청 처리(이미지 404)", ms)
                time.sleep(0.05)
        for _ in range(n(20)):
            sample("0_공통/일일 리포트", lambda: report.run(ctx))
        for _ in range(n(20)):
            sample("0_공통/미분류 신호 점검", lambda: unmatched.run(ctx))

    def g1():
        for cmd in ("/status", "/help", "/knowledge 429", "/flags"):
            for _ in range(n(100)):
                sample(f"1_기본/{cmd}", lambda: text(cmd))
        for _ in range(n(100)):
            sample("1_기본/텔레그램 getMe", lambda: real.call("getMe", {}, retry=False))
        for _ in range(n(50)):
            sample("1_기본/텔레그램 getUpdates(대기 0초)",
                   lambda: real.call("getUpdates", {"offset": 0, "timeout": 0, "allowed_updates": ["message", "callback_query"]}, retry=False))
        for i in range(n(10)):
            body = sample("1_기본/텔레그램 sendMessage",
                          lambda: real.call("sendMessage", {"chat_id": admin, "text": f"[LOCAL] 벤치마크 측정 메시지 {i + 1} · 곧 지웁니다"}, retry=False))
            mid = ((body or {}).get("result") or {}).get("message_id")
            if mid:
                sample("1_기본/텔레그램 deleteMessage", lambda: real.call("deleteMessage", {"chat_id": admin, "message_id": mid}, retry=False))

    def g2():
        for i in range(n(10)):
            traffic("ok", 1)
        time.sleep(5)
        good, bad = ids("bench-ok-%", n(10)), ids("bench-rec-%", n(10))
        for rid in good:
            sample("2_요청추적/성공 요청", lambda: text(f"/trace {rid}"))
        for rid in bad:
            sample("2_요청추적/실패 요청", lambda: text(f"/trace {rid}"))

    def g3():
        key = "receipt.image_fetch.http_4xx_404"
        for _ in range(n(10)):
            db.execute("DELETE FROM request_results WHERE feature = 'receipt' AND step_id = 'image_fetch' AND ts >= UTC_TIMESTAMP(3) - INTERVAL 20 MINUTE")
            db.execute("DELETE FROM alerts WHERE alert_key = %s", (key,))
            time.sleep(random.uniform(1, 61))
            last = traffic("missing", 20)
            row = wait_for("SELECT UNIX_TIMESTAMP(last_at) AS t FROM alerts WHERE alert_key = %s", (key,), 150)
            if row:
                value("3_즉시알림/요청 끝 → 알림 선점(실제 봇 감지)", (float(row["t"]) - last) * 1000)
            else:
                value("3_즉시알림/요청 끝 → 알림 선점(실제 봇 감지)", 150000, ok=False)
        fresh = time.time()
        for _ in range(n(20)):
            if time.time() - fresh > 150:
                fresh = traffic("missing", 20)
            sample("3_즉시알림/알림 답장 대화", lambda: reply("이거 원인이 뭐야? 우리 서버 코드 문제야?", f"alert:{key}@{int(time.time())}"))

    def g4():
        key = "receipt.image_fetch.http_5xx_500"
        hold_alert(key)
        fresh = traffic("broken", 20)
        incidents: list[str] = []
        for _ in range(n(10)):
            if time.time() - fresh > 150:
                hold_alert(key)
                fresh = traffic("broken", 20)
            sig = {"feature": "receipt", "step_id": "image_fetch", "reason": "http_5xx", "status": 500, "error_code": None, "ms": 60,
                   "ratio": 1.0, "fails": 20, "total": 20}
            payload = {"alert_key": key, "feature": "receipt", "step_id": "image_fetch", "reason": "http_5xx", "signal": sig,
                       "summary": "영수증 · 이미지 다운로드(image_fetch) 실패 서버 오류(500) 20/20건", "rank": 3, "drill": None,
                       "sample_ids": ids("bench-broken-%", 5), "knowledge_id": None, "detected_at": time.time()}
            sample("4_진단·지식/진단 리포트", lambda: run_event(ctx, new_event("alert.pipeline", payload, source="detector")))
            row = db.fetch_one("SELECT id FROM incidents ORDER BY created_at DESC LIMIT 1")
            if row:
                incidents.append(row["id"])
        kids: list[str] = []
        for i in range(n(20)):
            ref = f"incident:{incidents[i % len(incidents)]}" if incidents else None
            ask = "원인 확인했어. 이미지 서버(127.0.0.1:8799)의 /broken 경로가 500을 돌려준 거야. 이 내용으로 지식 추가해줘"
            sample("4_진단·지식/지식 추가 정리본", lambda: reply(ask, ref) if ref else text(ask))
            try:
                draft = expect("ks:")
            except RuntimeError as exc:
                log(f"  {exc}")
                continue
            sample("4_진단·지식/[저장] 추가", lambda: button(f"ks:{draft}"))
            m = re.search(r"KN_[A-Z0-9_]+", STATE["last"]["text"])
            if m:
                kids.append(m.group(0))
        for op, label, ask in (("수정", "4_진단·지식/지식 수정 정리본", "{} 조치에 \"이미지 서버 담당자에게 바로 알린다\"는 내용도 넣어줘"),
                               ("삭제", "4_진단·지식/지식 삭제 정리본", "{} 지식은 측정용이라 지워줘")):
            targets = kids[: n(10)] if op == "수정" else kids[n(10): n(20)] or kids[: n(10)]
            for kid in targets:
                sample(label, lambda: text(ask.format(kid)))
                try:
                    draft = expect("ks:")
                except RuntimeError as exc:
                    log(f"  {exc}")
                    continue
                sample(f"4_진단·지식/[저장] {op}", lambda: button(f"ks:{draft}"))

    def g5():
        hold_alert("receipt.luna_call.rate_limit_exceeded")
        now = time.time()
        repos.records.save_request_results([{"request_id": f"bench-429-{i:02d}-{uuid.uuid4().hex[:6]}", "ts": now - 5 * i, "task_id": "bench",
                                             "feature": "receipt", "result": "fail", "code": "UPSTREAM_FAILED", "step_id": "luna_call",
                                             "reason": "http_4xx", "status": 429, "error_code": "rate_limit_exceeded", "route": "luna",
                                             "latency_ms": 900} for i in range(20)])
        sig = {"feature": "receipt", "step_id": "luna_call", "reason": "http_4xx", "status": 429, "error_code": "rate_limit_exceeded",
               "ms": 900, "ratio": 1.0, "fails": 20, "total": 20}
        for _ in range(n(20)):
            payload = {"policy_key": "llm.max_concurrency", "knowledge_id": "KN_OPENAI_RATE_LIMIT", "alert_key": "receipt.luna_call.rate_limit_exceeded",
                       "message_id": None, "messages": [], "message_text": "", "signal": sig}
            sample("5_조정/조정 제안", lambda: run_event(ctx, new_event("signal.policy", payload, source="detector")))
            try:
                adj = expect("aa:")
            except RuntimeError as exc:
                log(f"  {exc}")
                continue
            sample("5_조정/[승인] 적용", lambda: button(f"aa:{adj}"))
            sample("5_조정/효과 판정·보고", lambda: run_event(ctx, new_event("adapt.observe", {"adj_id": adj}, source="scheduler")))
            if not repos.adjust.rollback(adj, "llm.max_concurrency"):
                repos.adjust.set_status(adj, "rejected")
            repos.adjust.delete_flag("llm.max_concurrency")
        follow.clear()

    def g6():
        file_ids = {}
        for fmt, name in FILES.items():
            with open(f"{BOX}/docs/{name}", "rb") as f:
                r = httpx.post(f"https://api.telegram.org/bot{real.token}/sendDocument", data={"chat_id": admin, "caption": "[LOCAL] 벤치마크용 · 곧 지웁니다"},
                               files={"document": (name, f)}, timeout=60).json()["result"]
            file_ids[fmt] = r["document"]["file_id"]
            real.call("deleteMessage", {"chat_id": admin, "message_id": r["message_id"]}, retry=False)
        for fmt in ("md", "txt", "docx"):
            document(file_ids[fmt], FILES[fmt])
        for fmt, name in FILES.items():
            ext = name.rsplit(".", 1)[1]
            for i in range(n(20)):
                fname = f"bench_{fmt}_{i:02d}.{ext}"
                sample(f"6_문서/업로드 {fmt}", lambda: document(file_ids[fmt], fname))
                sample("6_문서/[삭제]", lambda: button(f"dd:{doc_id(fname)}"))
        for _ in range(n(100)):
            sample("6_문서//docs", lambda: text("/docs"))
        for _ in range(n(100)):
            sample("6_문서//docs <이름>", lambda: text("/docs T1_택시미터_장애대응"))
        for q in ("택시미터에서 429 나면 동시 요청을 몇으로 낮추고 몇 분 뒤에 다시 봐?",
                  "영수증 이체 완료 판정이 계속 틀리면 어떻게 해야 해? 앞으로 생길 문제도 알려줘",
                  "혼잡도 지연이 8초 넘으면 누구한테 넘겨?", "결제 PG사 수수료율이 몇 퍼센트야?"):
            for _ in range(n(5)):
                sample("6_문서/문서 질문", lambda: text(q))
        for _ in range(n(10)):
            sample("6_문서/문서 정정 정리본", lambda: text("T1_택시미터_장애대응 문서의 429 절에서 동시 요청을 2가 아니라 3으로 낮추는 걸로 고쳐줘"))

    def g7():
        expected = "OpenAI 분당 요청 한도 초과(429 rate_limit_exceeded)"
        for _ in range(n(20)):
            sample("7_훈련/판정자(guess 채점)", lambda: nodes.env.judge(diag_prompts.JUDGE, expected, "OpenAI 분당 한도 초과로 429가 난다"))
        traffic_fn = nodes.actions.traffic
        nodes.actions.traffic = lambda *a, **k: None
        for _ in range(n(20)):
            sample("7_훈련//drill start", lambda: text("/drill start S-7a1c"))
            sample("7_훈련//drill stop", lambda: text("/drill stop"))
            db.execute("UPDATE drill_runs SET status = 'closed' WHERE status != 'closed'")
            repos.adjust.delete_flag("drill.luna_call")
        follow.clear()
        nodes.actions.traffic = traffic_fn
        start = time.time()
        text("/drill start S-7a1c")
        first = wait_for("SELECT UNIX_TIMESTAMP(MIN(ts)) AS t FROM request_results WHERE drill = TRUE AND ts >= FROM_UNIXTIME(%s)", (start,), 60)
        alert = wait_for("SELECT UNIX_TIMESTAMP(last_at) AS t FROM alerts WHERE alert_key = 'drill.receipt.luna_call.rate_limit_exceeded' "
                         "AND last_at >= FROM_UNIXTIME(%s)", (start,), 300)
        inc = wait_for("SELECT UNIX_TIMESTAMP(created_at) AS t FROM incidents WHERE drill = TRUE AND created_at >= FROM_UNIXTIME(%s)", (start,), 400)
        if first and alert:
            value("7_훈련/첫 합성 요청 → 감지(실제 봇)", (float(alert["t"]) - float(first["t"])) * 1000)
        if alert and inc:
            value("7_훈련/감지 → 진단 저장(실제 봇)", (float(inc["t"]) - float(alert["t"])) * 1000)
        wait_for("SELECT 1 AS x FROM (SELECT COUNT(*) AS c FROM request_results WHERE drill = TRUE AND ts >= FROM_UNIXTIME(%s)) t WHERE c >= 20",
                 (start,), 420)
        sample("7_훈련//drill guess", lambda: text("/drill guess OpenAI 분당 요청 한도 초과로 429가 나는 것 같다"))
        follow.clear()
        sample("7_훈련//drill stop(실제)", lambda: text("/drill stop"))
        compare = {}
        for ev, _ in follow:
            compare.setdefault(ev["payload"]["drill"]["config"], ev["payload"])
        follow.clear()
        for config in ("single", "rag", "agent"):
            for i in range(n(10)):
                payload = {**compare[config], "drill": {**compare[config]["drill"], "run_no": i + 1}, "detected_at": time.time()}
                sample(f"7_훈련/비교 실행 {config}", lambda: run_event(ctx, new_event("alert.pipeline", payload, source="followup", chain_depth=1)))
        text("/drill guess OpenAI 429 분당 한도 초과")
        repos.adjust.delete_flag("drill.luna_call")

    def g8():
        cases = json.loads(Path(f"{BOX}/decisions.json").read_text())
        questions = [c["text"] for c in cases["intent"] + cases["kind"]]
        for i in range(n(50)):
            sample("8_판단모델/판단 1회(의도·종류·도구 필요)", lambda: nodes.env.decider.route(questions[i % len(questions)]))
        acc = {}
        for name in ("intent", "kind"):
            rows = []
            for c in cases[name]:
                r = nodes.env.decider.route(c["text"]) or {}
                pick = (r.get("raw") or {}).get(name) or {}
                rows.append({"text": c["text"], "expect": c["expect"], "choice": pick.get("choice"), "confidence": pick.get("confidence"),
                             "used": r.get(name)})
            acc[name] = {"n": len(rows), "raw_correct": sum(x["choice"] == x["expect"] for x in rows),
                         "used_correct": sum(x["used"] == x["expect"] for x in rows), "fallback": sum(x["used"] is None for x in rows),
                         "used_wrong": sum(x["used"] is not None and x["used"] != x["expect"] for x in rows), "rows": rows}
        EXTRA["decision_accuracy"] = acc

    def g9():
        sent: list[int] = []

        class Sender:
            def send(self, chat, body, buttons=None, reply_to=None, html=False):
                res = real.call("sendMessage", {"chat_id": admin, "text": f"[LOCAL] 벤치마크 · {body} · 곧 지웁니다"}, retry=False)
                mid = ((res or {}).get("result") or {}).get("message_id")
                if mid:
                    sent.append(mid)
                return mid

        q = Scheduler(None, "bench", Sender(), **s.section("queue"))
        user_max = q.user_max
        for i in range(n(user_max)):
            ev = new_event("user.message", {"text": f"질문 {i + 1}", "message_id": 1}, source="telegram", from_id=admin, chat_id=admin)
            sample("9_대기열/질문 → 접수 안내 발송", lambda: q.enqueue(ev))
        full = new_event("user.message", {"text": f"질문 {user_max + 1}", "message_id": 1}, source="telegram", from_id=admin, chat_id=admin)
        rejected = not q.enqueue(full)
        dup = new_event("user.message", {"text": "질문 1", "message_id": 1}, source="telegram", from_id=admin, chat_id=admin)
        q.get()
        dup_rejected = not q.enqueue(dup)
        q.enqueue(new_event("alert.pipeline", {}, source="detector"))
        first = q.get()["type"]
        EXTRA["queue"] = {"user_max": user_max, "rejected_over_max": rejected, "dedupe_rejected": dup_rejected, "first_after_alert": first}
        for mid in sent:
            real.call("deleteMessage", {"chat_id": admin, "message_id": mid}, retry=False)

    raw = json.loads(Path(RAW_IN).read_text()) if Path(RAW_IN).exists() else {}
    for key, rows in (raw.get("samples") or {}).items():
        if key.split("_")[0] not in groups:
            SAMPLES[key] = rows
    for key, rows in (raw.get("messages") or {}).items():
        if key.split("_")[0] not in groups:
            MESSAGES[key] = rows
    LLM_CALLS.extend(c for c in raw.get("llm") or [] if str(c.get("key", "")).split("_")[0] not in groups)
    EXTRA.update({k: v for k, v in (raw.get("extra") or {}).items() if not ({"decision_accuracy": "8", "queue": "9"}.get(k) in groups)})
    for g in groups:
        log(f"묶음 {g} 시작")
        try:
            {"0": g0, "1": g1, "2": g2, "3": g3, "4": g4, "5": g5, "6": g6, "7": g7, "8": g8, "9": g9}[g]()
        except Exception as exc:
            log(f"묶음 {g} 중단: {type(exc).__name__}: {str(exc)[:200]}")
        save(s)
        log(f"묶음 {g} 끝")
    log("DONE")


def stats(vals: list[float]) -> dict | None:
    v = sorted(vals)
    if not v:
        return None
    q = statistics.quantiles(v, n=100, method="inclusive") if len(v) > 1 else [v[0]] * 99
    return {"n": len(v), "mean": statistics.fmean(v), "min": v[0], "p50": q[49], "p90": q[89], "p95": q[94], "p99": q[98], "max": v[-1],
            "std": statistics.pstdev(v) if len(v) > 1 else 0.0}


def summarize(samples: dict) -> dict:
    out = {}
    for key, rows in samples.items():
        good = [r for r in rows if r["ok"]]
        steps = list(dict.fromkeys(k for r in good for k in r["steps"]))
        cats = {c: statistics.fmean([r["cats"].get(c, 0) for r in good]) for c in CATS + ("http",) if any(r["cats"].get(c) for r in good)} if good else {}
        out[key] = {"total": stats([r["ms"] for r in good]), "fail": len(rows) - len(good),
                    "goal_fail": sum(1 for r in good if r["extra"].get("goal_fail")),
                    "w2_invalid": sum(1 for r in good if r["extra"].get("w2_valid") is False),
                    "steps": {k: stats([r["steps"][k] for r in good if k in r["steps"]]) for k in steps},
                    "cats": cats, "tin": statistics.fmean([r["tin"] for r in good]) if good else 0,
                    "tout": statistics.fmean([r["tout"] for r in good]) if good else 0, "llm": statistics.fmean([r["llm"] for r in good]) if good else 0,
                    "w1_rounds": statistics.fmean([r["extra"]["w1_rounds"] for r in good if "w1_rounds" in r["extra"]] or [0]),
                    "w2_retries": statistics.fmean([r["extra"]["w2_retries"] for r in good if "w2_retries" in r["extra"]] or [0])}
    return out


def save(s) -> None:
    from app.Server_Monitor_Bot.runtime.runner import image_tag

    raw = {"meta": {"date": time.strftime("%Y-%m-%d %H:%M:%S %z"), "model": s.section("model"), "budget": s.section("budget"),
                    "detect": s.section("detect"), "agent": s.section("agent"), "queue": s.section("queue"), "image_tag": image_tag(), "scale": SCALE,
                    "note": "흐름 시간에는 텔레그램 발송이 빠져 있다(측정 중 봇 토큰 없음). 발송 1회 지연은 1_기본/텔레그램 sendMessage"},
           "samples": SAMPLES, "messages": MESSAGES, "llm": LLM_CALLS, "extra": EXTRA, "summary": summarize(SAMPLES)}
    Path(RAW).write_text(json.dumps(raw, ensure_ascii=False, indent=1, default=str))


def fmt(ms: float | None) -> str:
    if ms is None:
        return "-"
    return f"{ms:,.0f}ms" if ms < 1000 else f"{ms / 1000:,.1f}s"


def report(path: str, base: str | None = None) -> None:
    raw = json.loads(Path(path).read_text())
    old = json.loads(Path(base).read_text())["summary"] if base else {}
    groups: dict[str, list] = collections.defaultdict(list)
    for key in raw["summary"]:
        groups[key.split("/", 1)[0]].append(key)
    for g in sorted(groups):
        print(f"\n### {g}\n")
        print("| 단계 | n | 실패 | 목표 미달 | W2 검증 실패 | 평균 | 최소 | p50 | p90 | p95 | p99 | 최대 | 표준편차 | LLM 호출 | 토큰(입/출) |" + (" v1 p50 | v1 p95 |" if old else ""))
        print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|" + ("---|---|" if old else ""))
        for key in groups[g]:
            sm, t = raw["summary"][key], raw["summary"][key]["total"] or {}
            row = [key.split("/", 1)[1], t.get("n", 0), sm["fail"], sm.get("goal_fail", 0), sm.get("w2_invalid", 0), *(fmt(t.get(k)) for k in ("mean", "min", "p50", "p90", "p95", "p99", "max", "std")),
                   f"{sm['llm']:.1f}", f"{sm['tin']:,.0f}/{sm['tout']:,.0f}"]
            if old:
                o = (old.get(key) or {}).get("total") or {}
                row += [fmt(o.get("p50")), fmt(o.get("p95"))]
            print("| " + " | ".join(map(str, row)) + " |")
        for key in groups[g]:
            sm = raw["summary"][key]
            if not sm["steps"] or not sm["total"]:
                continue
            print(f"\n{key.split('/', 1)[1]} — 하위 단계 (W1 조사 평균 {sm['w1_rounds']:.1f}회 · W2 다시 쓰기 평균 {sm['w2_retries']:.1f}회)\n")
            print("| 하위 단계 | n | 평균 | p50 | p95 | p99 | 최대 | 전체 평균 대비 |")
            print("|---|---|---|---|---|---|---|---|")
            for k, st in sm["steps"].items():
                print(f"| {k} | {st['n']} | {fmt(st['mean'])} | {fmt(st['p50'])} | {fmt(st['p95'])} | {fmt(st['p99'])} | {fmt(st['max'])} | "
                      f"{st['mean'] / sm['total']['mean'] * 100:.0f}% |")
            if sm["cats"]:
                total = sm["total"]["mean"]
                parts = " · ".join(f"{c} {fmt(v)}({v / total * 100:.0f}%)" for c, v in sorted(sm["cats"].items(), key=lambda x: -x[1]))
                print(f"\n시간 구성(평균): {parts} · 나머지 {fmt(max(total - sum(sm['cats'].values()), 0))}")


def run(groups: list[str], out: str) -> None:
    sh = lambda *cmd, **k: subprocess.run(cmd, check=True, **k)
    sh("docker", "exec", CONTAINER, "mkdir", "-p", f"{BOX}/docs")
    sh("docker", "cp", str(HERE), f"{CONTAINER}:{BOX}/bench.py")
    for name in FILES.values():
        sh("docker", "cp", str(DOCS / name), f"{CONTAINER}:{BOX}/docs/{name}")
    sh("docker", "cp", str(OK_IMAGE), f"{CONTAINER}:{BOX}/ok.png")
    sh("docker", "cp", str(DECISIONS), f"{CONTAINER}:{BOX}/decisions.json")
    sh("docker", "exec", CONTAINER, "rm", "-f", RAW, RAW_IN)
    if Path(out).exists():
        sh("docker", "cp", out, f"{CONTAINER}:{RAW_IN}")
    sh("docker", "exec", "-e", "BENCH_TG_TOKEN", "-e", f"BENCH_SCALE={SCALE}", "-w", "/app", "-e", "PYTHONPATH=/app", CONTAINER,
       "python", f"{BOX}/bench.py", "inner", *groups)
    sh("docker", "cp", f"{CONTAINER}:{RAW}", out)


def main() -> None:
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "inner":
        inner(args or list(GROUPS))
    elif cmd == "run":
        out = os.environ.get("BENCH_OUT", "bench_raw.json")
        run([a for a in args if a in GROUPS] or list(GROUPS), out)
    elif cmd == "report":
        report(args[0], args[1] if len(args) > 1 else None)
    else:
        sys.exit("run [묶음…] | report <원자료.json> [비교.json]")


if __name__ == "__main__":
    main()
