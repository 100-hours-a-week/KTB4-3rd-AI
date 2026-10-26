# 라우터는 app.state.scheduler.submit(kind, image_URL) 결과를 응답 모델로 감싼다. 가짜 scheduler를 쓴다.
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.features.receipt.router import router as receipt_router
from app.features.taxi_meter.router import router as taxi_router


class Scheduler:
    def __init__(self):
        self.calls = []

    async def submit(self, kind, image_url):
        self.calls.append((kind, image_url))
        if kind == "taxi_meter":
            return {"cost": 3000}
        return {"amount_cost": 6700, "transfer_info": "이체 완료", "sender_name": "김홍엽", "send_time": None}


def test_routers_submit_to_scheduler():
    app = FastAPI()
    app.include_router(taxi_router)
    app.include_router(receipt_router)
    app.state.scheduler = Scheduler()
    with TestClient(app) as client:
        assert client.post("/taxi_meter", json={"image_URL": "u1"}).json() == {"success": True, "cost": 3000}
        res = client.post("/receipt", json={"image_URL": "u2"})
        assert res.status_code == 200 and res.json()["send_time"] is None
        assert client.post("/taxi_meter", json={}).status_code == 422
        assert client.post("/receipt", json={"image_URL": 1}).status_code == 422
    assert app.state.scheduler.calls == [("taxi_meter", "u1"), ("receipt", "u2")]
