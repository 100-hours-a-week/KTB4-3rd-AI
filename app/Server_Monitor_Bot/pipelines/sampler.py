# P2 샘플(1분): 컨테이너 메모리·분석 프로세스 RSS·처리 중 요청 → resource_samples, 모니터 상태 1분 행 → monitor_health.
import resource
import sys
import time
from pathlib import Path

CGROUP_FILES = ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory/memory.usage_in_bytes")


def container_memory() -> int:
    for path in CGROUP_FILES:
        try:
            return int(Path(path).read_text().strip())
        except (OSError, ValueError):
            continue
    return 0


def rss_bytes() -> int:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def sample(ctx) -> None:
    worker = ctx.stats.get("worker") or {}
    rss = rss_bytes()
    ctx.repos.records.save_resource_sample(ctx.task_id, container_memory(), rss, int(worker.get("inflight", 0)))
    ctx.repos.records.save_health({"ts": time.time(), "task_id": ctx.task_id, "dropped": worker.get("dropped", 0),
                                   "orphan": worker.get("orphan", 0), "send_fail": ctx.stats.get("send_fail", 0),
                                   "restarts": ctx.stats.get("restarts", 0), "rss_bytes": rss, "image_tag": ctx.image_tag})
