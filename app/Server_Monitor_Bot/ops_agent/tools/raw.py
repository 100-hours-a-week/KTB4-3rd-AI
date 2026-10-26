# 대화용 원시 도구 4개: 요청 추적, 최근 경고 로그, 코드 검색, 코드 읽기. 코드는 조회만 한다.
from pathlib import Path

from langchain_core.tools import tool

from ..common.evidence import wrap
from .registry import ctx

APP = Path(__file__).resolve().parents[3]
SKIP = "Server_Monitor_Bot"


def _rel(path: Path) -> str:
    return f"app/{path.relative_to(APP).as_posix()}"


def _safe_path(file: str) -> Path | None:
    name = file.removeprefix("app/").lstrip("/")
    path = (APP / name).resolve()
    if APP not in path.parents or SKIP in path.parts or path.suffix != ".py" or not path.is_file():
        return None
    return path


def trace(request_id: str) -> dict:
    try:
        r = ctx().repos.records.request_trace(request_id)
    except Exception as exc:
        return {"error": f"request_trace 실패: {type(exc).__name__}"}
    res = r["result"]
    if not res:
        return {"error": f"{request_id} 기록 없음(30일 보존, 사용자 단위 추적 안 함)"}
    summary = (f"{res['feature']} {res['result']} code={res['code']} step={res['step_id']} reason={res['reason']} "
               f"route={res['route']}({res['route_reason']}) {res['latency_ms']}ms, LLM {len(r['llm_calls'])}건")
    return wrap(f"req:{request_id}", summary, r)


def logs(minutes: int = 10, query: str | None = None) -> dict:
    try:
        rows = ctx().repos.records.recent_logs(minutes, query)
    except Exception as exc:
        return {"error": f"recent_logs 실패: {type(exc).__name__}"}
    if not rows:
        return wrap("log:none", "해당 로그 없음", [])
    return wrap(",".join(f"log:{r['id']}" for r in rows[:5]), f"{len(rows)}줄, 최근: {rows[0]['message'][:120]}", rows)


def code_search(query: str) -> dict:
    q = query.lower().strip()
    if not q:
        return {"error": "query가 비었음"}
    hits = []
    for path in sorted(APP.rglob("*.py")):
        if SKIP in path.parts:
            continue
        for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            if q in line.lower():
                hits.append({"file": _rel(path), "line": n, "text": line.strip()[:160]})
                if len(hits) >= 20:
                    break
        if len(hits) >= 20:
            break
    if not hits:
        return wrap("code:none", f"'{query}' 없음", [])
    return wrap(",".join(f"code:{h['file']}:{h['line']}" for h in hits[:5]), f"{len(hits)}곳", hits)


def code_read(file: str, line: int = 1, span: int = 40) -> dict:
    path = _safe_path(file)
    if path is None:
        return {"error": f"읽을 수 없는 경로: {file}"}
    span = max(1, min(int(span), 60))
    lines = path.read_text(errors="ignore").splitlines()
    start = max(1, int(line) - span // 2)
    chunk = [f"{n}: {lines[n - 1]}" for n in range(start, min(len(lines), start + span - 1) + 1)]
    return wrap(f"code:{_rel(path)}:{line}", f"{_rel(path)} {start}~{start + len(chunk) - 1}행",
                {"file": _rel(path), "lines": chunk, "cited": [f"{_rel(path)}:{n}" for n in range(start, start + len(chunk))]})


@tool(description="요청 하나(request_id)의 결과·관련 로그·LLM 호출. 30일 보존, 사용자 단위 추적은 안 한다.")
def request_trace(request_id: str) -> dict:
    return trace(request_id)


@tool(description="최근 WARNING 이상 로그. minutes: 1~10, query: 전문 검색어(선택).")
def recent_logs(minutes: int = 10, query: str | None = None) -> dict:
    return logs(minutes, query)


@tool(description="서비스 코드 문자열 검색(app/, 모니터 코드 제외). 결과 최대 20곳(파일:줄).")
def search_code(query: str) -> dict:
    return code_search(query)


@tool(description="코드 조각 읽기. file: app/... 경로, line: 가운데 줄, span: 1~60.")
def read_code(file: str, line: int = 1, span: int = 40) -> dict:
    return code_read(file, line, span)


ALL = (request_trace, recent_logs, search_code, read_code)
