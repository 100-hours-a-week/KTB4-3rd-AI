# 미터기·송금 Kafka 분석: 'meter/transfer analysis finished' 로그로 요청 기록을 확정하고 결과 상태·코드를 단계·이유로 바꾼다. Kafka 몫은 내부 큐 KAFKA_SLOTS, 종료 대기는 10초다.
import time

from app.Server_Monitor_Bot.hooks.assemble import Assembler

H, IMG, Q = "app.features.taxi_meter.kafka_handler", "app.features.taxi_meter.image", "app.inner_queue.scheduler"


def log(message, rid, level="INFO", logger=H, ago=0.0):
    return {"ts": time.time() - ago, "level": level, "logger": logger, "message": message, "request_id": rid, "exc": None}


def test_meter_kafka_results_are_recorded():
    a = Assembler(outbox=None)
    for item in (
        log("request route: kind=taxi_meter route=api reason=route_api", "ok", logger=Q, ago=4),
        log("meter analysis finished: settlement_id=1 status=READ code=-", "ok"),
        log("image download failed: host=s3 reason=http_4xx status=404 ms=12", "nf", "WARNING", IMG, ago=1),
        log("meter analysis finished: settlement_id=2 status=SYSTEM_FAILURE code=IMAGE_FETCH_FAILED", "nf"),
        log("meter analysis finished: settlement_id=3 status=UNREADABLE code=JUDGE_PARSE_FAILED", "bad"),
        log("meter analysis skipped: settlement_id=4 waited=120s reason=queue_wait_exceeded", "late", "WARNING"),
        log("meter analysis finished: settlement_id=4 status=SYSTEM_FAILURE code=QUEUE_WAIT_EXCEEDED", "late"),
        log("meter analysis finished: settlement_id=5 status=SYSTEM_FAILURE", "x"),
    ):
        a.handle("log", item)
    assert a.settle(time.time()) == []
    rows = {x["request_id"]: x for kind, x in a.settle(time.time() + 10) if kind == "request_result"}
    assert {r["feature"] for r in rows.values()} == {"taxi_meter"} and not a.bags
    assert (rows["ok"]["result"], rows["ok"]["code"], rows["ok"]["route"]) == ("ok", "OK", "api") and 3500 <= rows["ok"]["latency_ms"] < 6000
    assert (rows["nf"]["code"], rows["nf"]["step_id"], rows["nf"]["status"], rows["nf"]["image_host"]) == ("IMAGE_FETCH_FAILED", "image_fetch", 404, "s3")
    assert (rows["bad"]["result"], rows["bad"]["step_id"]) == ("fail", "luna_parse")
    assert (rows["late"]["code"], rows["late"]["step_id"], rows["late"]["reason"]) == ("QUEUE_WAIT_EXCEEDED", "queue_wait", "expired")
    assert (rows["x"]["code"], rows["x"]["step_id"]) == ("INTERNAL", "unhandled")


def test_transfer_kafka_results_are_recorded_as_receipt():
    a = Assembler(outbox=None)
    t, img = "app.features.receipt.kafka_handler", "app.features.receipt.image"
    for item in (
        log("transfer analysis finished: settlement_id=1 user_id=7 attempt=1 status=READ code=-", "t-ok", logger=t),
        log("image download failed: host=s3 reason=http_4xx status=403 ms=9", "t-ex", "WARNING", img, ago=1),
        log("transfer analysis finished: settlement_id=2 user_id=7 attempt=2 status=SYSTEM_FAILURE code=IMAGE_FETCH_FAILED", "t-ex", logger=t),
        log("transfer analysis finished: settlement_id=3 user_id=8 attempt=1 status=UNREADABLE code=JUDGE_PARSE_FAILED", "t-bad", logger=t),
    ):
        a.handle("log", item)
    rows = {x["request_id"]: x for kind, x in a.settle(time.time() + 10) if kind == "request_result"}
    assert {r["feature"] for r in rows.values()} == {"receipt"} and not a.bags
    assert (rows["t-ok"]["result"], rows["t-ok"]["code"]) == ("ok", "OK")
    assert (rows["t-ex"]["code"], rows["t-ex"]["step_id"], rows["t-ex"]["status"]) == ("IMAGE_FETCH_FAILED", "image_fetch", 403)
    assert (rows["t-bad"]["result"], rows["t-bad"]["step_id"]) == ("fail", "luna_parse")


def test_meter_worker_uses_kafka_slots_and_short_stop():
    import pytest
    pytest.importorskip("aiokafka")
    from app.features.image_analysis.kafka_consumer import create_worker
    from app.inner_queue.scheduler import API_WORKERS, HTTP_RESERVE, KAFKA_SLOTS, Scheduler
    from app.kafka.config import KafkaSettings
    scheduler = Scheduler({})
    worker = create_worker(KafkaSettings("localhost:9092"), scheduler, {})
    assert KAFKA_SLOTS == API_WORKERS - HTTP_RESERVE == worker.max_in_flight == 12 and worker.shutdown_timeout == 10
    assert create_worker(KafkaSettings("localhost:9092"), scheduler, {"IMAGE_ANALYSIS_MAX_IN_FLIGHT": "3"}).max_in_flight == 3
