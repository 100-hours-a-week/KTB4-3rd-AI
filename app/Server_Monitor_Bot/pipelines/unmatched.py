# 미분류 신호 점검(리더, 매일): 최근 24시간 실패 신호 중 어떤 active 지식에도 안 맞는 것을 지식 추가 후보로 보낸다.
from ..channels.telegram import render
from ..knowledge.schema import best


def find(signals: list[dict], knowledge: list[dict]) -> list[dict]:
    return [s for s in signals
            if best(knowledge, {**s, "ms": float("inf"), "ratio": float("inf"), "heads": []}) is None]


def run(ctx) -> list[dict]:
    rows = find(ctx.repos.metrics.signals(24), ctx.repos.knowledge.active())
    if rows and ctx.telegram:
        ctx.telegram.send(None, render.unmatched(rows))
    return rows
