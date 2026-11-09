# 서버 장애 모니터링 봇 로컬 시연용 장애 만들기(사용법: README.md). .venv/bin/python tools/server_monitor/demo.py serve|ok|missing|slow|broken|rate429 [n]
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).parent
PORT = int(os.environ.get("DEMO_IMAGE_PORT", "8766"))
APP = os.environ.get("DEMO_APP_URL", "http://127.0.0.1:8000")
CONTAINER = os.environ.get("DEMO_CONTAINER", "sbm-app")
IMAGES = f"http://host.docker.internal:{PORT}"
OK_IMAGE = ROOT / "samples" / "ok.png"


class Images(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/ok"):
            body = OK_IMAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/broken"):
            self.send_response(500)
            self.end_headers()
        elif self.path.startswith("/slow"):
            time.sleep(8)
            self.send_response(200)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def traffic(path: str, n: int) -> None:
    def one(i: int):
        rid = f"demo-{path}-{i:02d}-{uuid.uuid4().hex[:6]}"
        res = httpx.post(f"{APP}/receipt", json={"image_URL": f"{IMAGES}/{path}.png"}, headers={"X-Request-ID": rid}, timeout=70)
        body = res.json()
        print(f"{rid} {res.status_code} {body.get('code') or 'OK'}", flush=True)

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(one, range(n)))


RATE429 = """
import sys, time, uuid
from app.Server_Monitor_Bot.config.settings import load
from app.Server_Monitor_Bot.store.mysql import MySQLDB
from app.Server_Monitor_Bot.store.repos.records import Records
n, now = int(sys.argv[1]), time.time()
rows = [{"request_id": f"demo-429-{i:02d}-{uuid.uuid4().hex[:6]}", "ts": now - 5 * i, "task_id": "demo", "feature": "receipt",
         "result": "fail", "code": "UPSTREAM_FAILED", "step_id": "luna_call", "reason": "http_4xx", "status": 429,
         "error_code": "rate_limit_exceeded", "route": "luna", "latency_ms": 900,
         "image_host": "host.docker.internal"} for i in range(n)]
print(Records(MySQLDB.from_url(load().db_url, max_connections=1)).save_request_results(rows), "rows")
"""


def main() -> None:
    cmd, n = sys.argv[1] if len(sys.argv) > 1 else "", int(sys.argv[2]) if len(sys.argv) > 2 else 20
    if cmd == "serve":
        print(f"images on :{PORT} (ok, missing, slow, broken)", flush=True)
        ThreadingHTTPServer(("127.0.0.1", PORT), Images).serve_forever()
    elif cmd in ("ok", "missing", "slow", "broken"):
        traffic(cmd, n if len(sys.argv) > 2 or cmd != "ok" else 1)
    elif cmd == "rate429":
        subprocess.run(["docker", "exec", "-i", CONTAINER, "python", "-", str(n)], input=RATE429, text=True, check=True)
    else:
        sys.exit("serve | ok | missing | slow | broken | rate429 [n]")


if __name__ == "__main__":
    main()
