# 분석 프로세스 진입점: 락·DB 연결 → 저장소·컨텍스트 구성 → P1~P9 쓰레드와 이벤트 소비(동시 실행 1). SIGTERM이면 버퍼를 비우고 끝낸다.
import fcntl
import json
import logging
import os
import queue
import random
import signal
import socket
import sys
import threading
import time
import urllib.request
from datetime import datetime
from logging.handlers import RotatingFileHandler

from ..channels.telegram import render
from ..channels.telegram.client import TelegramClient
from ..channels.telegram.render import KST
from ..channels.telegram.receiver import Receiver
from ..config.settings import load
from ..pipelines import collector, detector, flag_sync, knowledge_seed, report, retention, sampler, unmatched
from ..store.buffer import FlagCache, Outbox
from .context import Ctx
from .leader import Leader, backoff
from .scheduler import Scheduler

log = logging.getLogger("app.Server_Monitor_Bot")
SCHEMA_VERSION = 1
RSS_LIMIT = 300 * 1024 * 1024
DAILY_HOUR = 9


def setup_logging(buffer_dir) -> None:
    log.propagate = False
    log.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) for h in log.handlers):
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        handler = RotatingFileHandler(buffer_dir / "monitor.log", maxBytes=5 * 1024 * 1024, backupCount=3)
        handler.setFormatter(fmt)
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(logging.WARNING)
        console.setFormatter(fmt)
        log.addHandler(handler)
        log.addHandler(console)


def image_tag() -> str:
    base = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
    if not base:
        return "local"
    try:
        with urllib.request.urlopen(f"{base}/task", timeout=2) as res:
            containers = json.load(res).get("Containers", [])
        chosen = next((c for c in containers if c.get("Name") == "fastapi"), containers[0] if containers else {})
        return (chosen.get("Image") or "unknown").rsplit(":", 1)[-1][:128]
    except Exception:
        return "unknown"


def every(stop: threading.Event, interval: float, fn, name: str, when=lambda: True) -> threading.Thread:
    def loop():
        fails = 0
        while not stop.is_set():
            if when():
                try:
                    fn()
                    fails = 0
                except Exception as exc:
                    fails += 1
                    log.warning("%s failed: error=%s fails=%s", name, type(exc).__name__, fails)
            stop.wait(backoff(interval, fails))

    t = threading.Thread(target=loop, name=f"server-bot-{name}", daemon=True)
    t.start()
    return t


def connect(settings, stop: threading.Event, telegram: TelegramClient):
    from ..store.mysql import MySQLDB

    if not settings.db_url:
        log.warning("analysis disabled: reason=AI_DB_URL missing")
        return None
    wait, started, alerted = 1.0, time.time(), False
    while not stop.is_set():
        try:
            db = MySQLDB.from_url(settings.db_url, max_connections=2)
            if db.ping():
                return db
        except Exception as exc:
            log.warning("db connect failed: error=%s", type(exc).__name__)
        if not alerted and time.time() - started >= 300:
            telegram.send(None, render.store_down(5))
            alerted = True
        stop.wait(wait + random.uniform(0, wait / 2))
        wait = min(wait * 2, 60)
    return None


def vector(settings):
    try:
        from ..store.embed import Embedder
        from ..store.vector import COLLECTIONS, VectorDB

        model = settings.section("model")
        vec = VectorDB(stage=settings.stage, url=settings.qdrant_url, api_key=settings.qdrant_key, dim=model.get("embedding_dim", 1536))
        embed = Embedder(settings.openai_key, model.get("embedding", "text-embedding-3-small"))
    except Exception as exc:
        log.warning("vector store unavailable: error=%s", type(exc).__name__)
        return None, None
    for name in COLLECTIONS:
        try:
            vec.ensure(name)
        except Exception as exc:
            log.warning("vector ensure failed: collection=%s error=%s", name, type(exc).__name__)
    return vec, embed


def consume(ctx, sched: Scheduler, stop: threading.Event, analysis: threading.Event) -> None:
    try:
        from ..ops_agent.main_graph import run_event
    except Exception as exc:
        log.warning("agent import failed: error=%s", type(exc).__name__)
        run_event = None
    while not stop.is_set():
        try:
            event = sched.get(timeout=1)
        except queue.Empty:
            continue
        if run_event and analysis.is_set():
            try:
                run_event(ctx, event)
            except Exception as exc:
                log.warning("event failed: type=%s error=%s", event.get("type"), type(exc).__name__)
        try:
            sched.done(event)
        except Exception as exc:
            log.warning("event done failed: error=%s", type(exc).__name__)


def daily(ctx, state: dict) -> None:
    now = datetime.now(KST)
    if now.hour < DAILY_HOUR or state.get("day") == now.date():
        return
    state["day"] = now.date()
    if not ctx.repos.coord.claim_alert(f"daily:{now:%Y-%m-%d}", 1, 1, ctx.task_id, quiet_s=86400):
        return
    for name, job in (("report", report.run), ("retention", retention.run), ("unmatched", unmatched.run)):
        try:
            job(ctx)
        except Exception as exc:
            log.warning("daily %s failed: error=%s", name, type(exc).__name__)


def main() -> None:
    log.propagate = False
    if not log.handlers:
        log.addHandler(logging.NullHandler())
    try:
        run()
    except Exception:
        log.exception("analysis crashed")


def run() -> None:
    settings = load()
    settings.buffer_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(settings.buffer_dir)
    lock = open(settings.buffer_dir / "analysis.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.info("analysis already running in this task")
        return
    try:
        os.nice(10)
    except OSError:
        pass
    stop, analysis = threading.Event(), threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    task_id, tag = socket.gethostname(), image_tag()
    stats = {"send_fail": 0, "restarts": 0}
    telegram = TelegramClient(settings.telegram_token, settings.admin_ids, f"[{settings.stage.upper()}]", stats)
    outbox = Outbox(settings.buffer_dir / "buffer.db", settings.section("buffer").get("max_mb", 50))
    db = connect(settings, stop, telegram)
    if db is None:
        return

    from ..store.repos import Repos

    vec, embed = vector(settings)
    repos = Repos(db, vec, embed, tuple(settings.policy))
    telegram.recipients = repos.members.chats
    if settings.discord_token and settings.discord_channel_id:
        from ..channels.discord.client import DiscordClient
        from ..channels.fanout import Channels

        telegram.recipients = lambda: [c for c in repos.members.chats() if c != settings.discord_channel_id]
        telegram = Channels(telegram, DiscordClient(settings.discord_token, settings.discord_channel_id, telegram.head, stats))
    sched = Scheduler(repos.coord, task_id, telegram, **settings.section("queue"))
    leader = Leader(db, settings.stage, stop)
    ctx = Ctx(settings=settings, repos=repos, telegram=telegram, task_id=task_id, image_tag=tag,
              enqueue=sched.enqueue, is_leader=leader.is_leader, stats=stats)
    p1 = collector.Collector(ctx, outbox)
    every(stop, 3, p1.tick, "p1")
    every(stop, 60, lambda: sampler.sample(ctx), "p2")
    try:
        version = repos.coord.schema_version()
    except Exception:
        version = 0
    if version != SCHEMA_VERSION:
        log.warning("schema mismatch: expected=%s actual=%s", SCHEMA_VERSION, version)
        telegram.send(None, render.schema_mismatch(SCHEMA_VERSION, version))
    else:
        analysis.set()
        flags = FlagCache(settings.buffer_dir / "flags.db")
        leader.start()
        threading.Thread(target=lambda: knowledge_seed.run(ctx), name="server-bot-p4", daemon=True).start()
        every(stop, settings.section("detect").get("interval_s", 15), detector.Detector(ctx).tick, "p3")
        every(stop, 3, lambda: flag_sync.run(ctx, flags), "p8")
        every(stop, 5, sched.pull, "p9", when=leader.is_leader)
        daily_state: dict = {}
        every(stop, 30, lambda: daily(ctx, daily_state), "daily", when=leader.is_leader)
        Receiver(ctx, stop).start()
        if settings.discord_token and settings.discord_channel_id:
            from ..channels.discord.receiver import DiscordReceiver

            DiscordReceiver(ctx, stop).start()
        threading.Thread(target=consume, args=(ctx, sched, stop, analysis), name="server-bot-events", daemon=True).start()
    while not stop.is_set():
        if sampler.rss_bytes() > RSS_LIMIT:
            log.warning("rss limit exceeded: rss=%s", sampler.rss_bytes())
            stop.set()
            break
        stop.wait(10)
    analysis.clear()
    p1.drain(10)
    log.info("analysis stopped")
