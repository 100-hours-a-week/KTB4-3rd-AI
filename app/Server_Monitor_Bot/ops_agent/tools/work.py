# 작업 도구 9개: 절차와 결과 형식이 고정된 읽기 전용 함수. 결과는 {evidence_id, summary, data} 또는 {error}.
import functools
import time
from datetime import datetime, timezone

from langchain_core.tools import tool

from ..common.evidence import wrap
from .registry import ctx, drill_mode, hide_knowledge


def _hm() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M")


def _safe(fn):
    @functools.wraps(fn)
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            return {"error": f"{fn.__name__} 실패: {type(exc).__name__}"}
    return run


@_safe
def step_failures(feature: str, step_id: str, minutes: int = 15) -> dict:
    r = ctx().repos.metrics.step_failures(feature, step_id, minutes, drill=drill_mode.get())
    now, before = r["now"], r["before"]
    summary = (f"{feature}.{step_id} 최근 {r['minutes']}분 실패 {now['fails']}/{now['total']} ({now['rate']:.0%}), "
               f"직전 같은 구간 {before['fails']}/{before['total']} ({before['rate']:.0%})")
    return wrap(f"metric:{feature}.{step_id}@{_hm()}", summary, r)


@_safe
def llm_calls(feature: str, minutes: int = 15) -> dict:
    r = ctx().repos.metrics.llm_summary(feature, minutes)
    if not r.get("calls"):
        return wrap(f"llm:{feature}@{_hm()}", f"{feature} 최근 {r['minutes']}분 LLM 호출 없음", r)
    summary = (f"{feature} LLM {r['calls']}건, 오류 {r['errors']}, p50 {r['p50_ms']}ms p95 {r['p95_ms']}ms, "
               f"재시도율 {r['retry_rate']:.0%}, 호출당 토큰 {r['tokens_per_call']}, 모델 {r['models']}")
    return wrap(",".join(f"llm:{i}" for i in r["sample_ids"]), summary, r)


@_safe
def dependency(dep: str, minutes: int = 15) -> dict:
    r = ctx().repos.metrics.dependency_failures(dep, minutes)
    if "error" in r:
        return r
    failing = ", ".join(f"{f['feature']}.{f['step_id']} {f['fails']}/{f['total']}" for f in r["failing"]) or "동반 실패 없음"
    return wrap(f"dep:{dep}@{_hm()}", f"{dep}(단계 {r['steps']}) 최근 {r['minutes']}분: {failing}", r)


@_safe
def resources(minutes: int = 30) -> dict:
    rows = ctx().repos.metrics.resource_trend(minutes)
    summary = "; ".join(f"{r['task_id'][:6]} mem {int(r['mem_max'] or 0) // 2**20}MB rss {int(r['rss_max'] or 0) // 2**20}MB "
                        f"inflight {r['inflight_max']}" for r in rows) or "샘플 없음"
    return wrap(f"res:all@{_hm()}", summary, rows)


@_safe
def recent_deploy(hours: int = 24) -> dict:
    rows = ctx().repos.metrics.deploy_changes(hours)
    if not rows:
        return wrap(f"deploy:none@{_hm()}", f"최근 {hours}시간 배포 기록 없음", [])
    summary = "; ".join(f"{r['image_tag']} 시작 {r['first_ts']:%H:%M}" for r in rows)
    return wrap(",".join(f"deploy:{r['image_tag']}" for r in rows), summary, rows)


@_safe
def knowledge_search(query: str, top_k: int = 5) -> dict:
    c = ctx()
    agent = c.settings.section("agent")
    rows = c.repos.knowledge.search(query, hide=list(hide_knowledge.get()), limit=min(int(top_k), 5),
                                    min_score=agent.get("search_min_score", 0.35))
    if not rows:
        return wrap(f"kn:none@{_hm()}", "관련 지식 없음", [])
    data = [{"id": k["id"], "version": k["version"], "title": k["title"], "cause": k["cause"], "resolution": k["resolution"],
             "checks": k["checks"], "policy_key": k["policy_key"], "score": k.get("score")} for k in rows]
    return wrap(",".join(f"kn:{k['id']}@v{k['version']}" for k in rows), "; ".join(f"{k['id']} {k['title']}" for k in rows), data)


@_safe
def chat_search(query: str, top_k: int = 5, incident_id: str | None = None) -> dict:
    c = ctx()
    rows = c.repos.chat.search(query, incident_id=incident_id, limit=min(int(top_k), 5),
                               min_score=c.settings.section("agent").get("search_min_score", 0.35))
    if not rows:
        return wrap(f"chat:none@{_hm()}", "관련 대화 없음", [])
    return wrap(",".join(f"chat:{r['message_id']}" for r in rows), "; ".join(r["question"][:60] for r in rows), rows)


@_safe
def flags() -> dict:
    rows = ctx().repos.adjust.flags()
    summary = "; ".join(f"{r['key']}={r['value']} (만료 {r['expires_at']})" for r in rows) or "활성 플래그 없음"
    return wrap(",".join(f"flag:{r['key']}" for r in rows) or f"flag:none@{_hm()}", summary, rows)


@_safe
def cloudwatch(filter_pattern: str, hours: int = 1) -> dict:
    c = ctx()
    if c.stage not in ("dev", "prod"):
        return {"error": "CloudWatch 조회는 dev·prod 태스크에서만 쓴다"}
    try:
        import boto3
    except ImportError:
        return {"error": "boto3가 설치되지 않음"}
    from ...hooks.mask import mask

    group = c.settings.section("cloudwatch").get("log_group", f"/ecs/moyeota-{c.stage}/fastapi")
    end = int(time.time() * 1000)
    res = boto3.client("logs").filter_log_events(logGroupName=group, filterPattern=filter_pattern,
                                                 startTime=end - min(int(hours), 24) * 3600 * 1000, endTime=end, limit=50)
    events = [{"id": e["eventId"], "ts": e["timestamp"], "message": mask(e["message"])[:500]} for e in res.get("events", [])]
    return wrap(",".join(f"cw:{e['id']}" for e in events[:5]) or f"cw:none@{_hm()}", f"{len(events)}건", events)


@tool(description="한 기능·단계의 실패율, 사유 분포 상위 5, 직전 같은 길이 구간과 비교. feature: receipt|taxi_meter, step_id: 단계 id, minutes: 1~60.")
def check_step_failures(feature: str, step_id: str, minutes: int = 15) -> dict:
    return step_failures(feature, step_id, minutes)


@tool(description="LLM 호출 요약: 호출 수, 상태·에러 코드 분포, 지연 p50·p95, 재시도율, 호출당 토큰, 모델 값. feature: receipt|taxi_meter|other, minutes: 1~60.")
def check_llm_calls(feature: str, minutes: int = 15) -> dict:
    return llm_calls(feature, minutes)


@tool(description="같은 외부 대상을 쓰는 기능·단계의 동반 실패(영향 범위). dep: image_host|openai|modal, minutes: 1~60.")
def check_dependency(dep: str, minutes: int = 15) -> dict:
    return dependency(dep, minutes)


@tool(description="태스크별 메모리·분석 프로세스 RSS·처리 중 요청 최대치. minutes: 1~60.")
def check_resources(minutes: int = 30) -> dict:
    return resources(minutes)


@tool(description="최근 배포 이미지 태그의 시작 시각과 전후 1시간 단계별 실패 수. hours: 1~24.")
def check_recent_deploy(hours: int = 24) -> dict:
    return recent_deploy(hours)


@tool(description="장애 지식(원인·해결·확인 방법) 의미 검색. query: 증상 설명 문장, top_k: 1~5.")
def search_knowledge(query: str, top_k: int = 5) -> dict:
    return knowledge_search(query, top_k)


@tool(description="과거 챗봇 대화(질문·답) 의미 검색. incident_id를 주면 그 장애 대화만.")
def search_chat(query: str, top_k: int = 5, incident_id: str | None = None) -> dict:
    return chat_search(query, top_k, incident_id)


@tool(description="지금 활성인 조정·훈련 플래그와 만료 시각.")
def active_flags() -> dict:
    return flags()


@tool(description="팀 CloudWatch 원본 로그 조회(보조, 내 DB에 없을 때만). filter_pattern: CloudWatch 필터 문법, hours: 1~24.")
def query_cloudwatch(filter_pattern: str, hours: int = 1) -> dict:
    return cloudwatch(filter_pattern, hours)


ALL = (check_step_failures, check_llm_calls, check_dependency, check_resources, check_recent_deploy, search_knowledge,
       search_chat, active_flags, query_cloudwatch)
