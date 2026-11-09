# P5 LLM 일일 리포트(리더, 매일 09:00 KST): 전날 서비스 luna 호출과 모니터 자신의 비용·상태를 보낸다.
from datetime import datetime, timedelta

from ..channels.telegram import render
from ..channels.telegram.render import KST


def yesterday_utc(now: datetime | None = None) -> tuple[str, datetime, datetime]:
    today = (now or datetime.now(KST)).astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=1)
    to_utc = lambda d: (d - d.utcoffset()).replace(tzinfo=None)
    return start.strftime("%Y-%m-%d"), to_utc(start), to_utc(today)


def run(ctx) -> str:
    day, start, end = yesterday_utc()
    text = render.daily(day, ctx.repos.metrics.daily(start, end))
    if ctx.telegram:
        ctx.telegram.send(None, text)
    return text
