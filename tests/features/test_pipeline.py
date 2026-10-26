# model_pipe_run과 choose가 await llm(...)으로 같은 판정을 내는지 본다. llm은 가짜다.
import asyncio

from app.core import oj_decide
from app.core.oj_decide import Decision
from app.features.receipt import pipeline as receipt
from app.features.taxi_meter import pipeline as taxi


def fake_llm(rows):
    async def llm(name, prompt=None, image=None, device="cpu", schema=None):
        return "요금 3,000원" if name == "ocr" else rows
    return llm


def test_taxi_model_pipe_run(monkeypatch):
    monkeypatch.setattr(taxi, "llm", fake_llm([[0.01, 0.96, 0.03]]))
    assert asyncio.run(taxi.model_pipe_run(None, "cpu")) == {"cost": 3000}
    monkeypatch.setattr(taxi, "llm", fake_llm([[0.30, 0.50, 0.20]]))
    assert asyncio.run(taxi.model_pipe_run(None, "cpu")) is None


def test_choose(monkeypatch):
    monkeypatch.setattr(oj_decide, "llm", fake_llm([[0.01, 0.96, 0.03], [0.70, 0.20, 0.10]]))
    assert asyncio.run(oj_decide.choose("원문", "표시된 운임", ["3,000원", "114원"], "cpu")) == Decision("pass", "3,000원")
    assert asyncio.run(oj_decide.choose("원문", "표시된 운임", [], "cpu")) == Decision("review", None)


def test_receipt_model_pipe_run(monkeypatch):
    picks = {"영수증 합계 금액": "6,700원", "보낸 날짜와 시각": "2026-09-2312:30", "이체 완료 정보": "이체 완료", "받는 사람": "김홍엽"}

    async def choose(plain, field, candidates, device):
        return Decision("pass", picks[field])

    monkeypatch.setattr(receipt, "llm", fake_llm(None))
    monkeypatch.setattr(receipt, "choose", choose)
    assert asyncio.run(receipt.model_pipe_run(None, "cpu")) == {
        "amount_cost": 6700, "send_time": "2026-09-23 12:30", "transfer_info": "이체 완료", "sender_name": "김홍엽"}
    picks["이체 완료 정보"] = "예약 이체"
    assert asyncio.run(receipt.model_pipe_run(None, "cpu")) is None
