from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


class StoppedWorker:
    alive = False


def test_health_is_503_when_kafka_worker_stopped() -> None:
    app.state.kafka_workers = [StoppedWorker()]
    try:
        response = TestClient(app).get("/health")
    finally:
        app.state.kafka_workers = []

    assert response.status_code == 503
    assert response.json() == {"status": "kafka_worker_down"}
