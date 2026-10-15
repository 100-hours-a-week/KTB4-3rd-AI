# /metrics가 Cloud 대시보드·알림이 쓰는 기본 지표 이름과 라벨로 나오는지 본다. 네트워크는 쓰지 않는다.
from fastapi.testclient import TestClient

from app.main import app


def test_metrics():
    with TestClient(app) as client:
        client.get("/health")
        client.post("/taxi_meter", json={})
        res = client.get("/metrics")
    assert res.status_code == 200
    assert 'http_requests_total{handler="/taxi_meter",method="POST",status="4xx"}' in res.text
    assert 'http_request_duration_seconds_bucket{handler="/taxi_meter"' in res.text
    assert 'handler="/health"' not in res.text and 'handler="/metrics"' not in res.text
