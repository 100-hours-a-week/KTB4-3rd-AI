# 훅 공용 상태: 설치 여부, 스테이지, 큐, 버퍼 경로.
import queue
from pathlib import Path

INSTALLED = False
STAGE = ""
DIR = Path("/tmp/server_bot")
QUEUE: "queue.Queue[tuple[str, dict]]" = queue.Queue(maxsize=10000)
STATS = {"dropped": 0, "orphan": 0}
