# 판단 모델: OpenAI Decisions API(/v1/decisions)로 대화 의도·질문 종류·도구 필요 여부와 훈련 원인 적중을 한 번에 판단한다. 실패하거나 확신이 낮으면 None.
import logging
import time

from ...hooks.mask import mask

log = logging.getLogger("app.Server_Monitor_Bot.decide")
INTENTS = {
    "answer": "질문에 답하기(상태·원인·요청 추적·사용법·문서 내용을 묻는 질문)",
    "knowledge_add": "방금 대화나 장애 내용을 장애 지식으로 새로 추가해 달라는 요청",
    "knowledge_update": "기존 장애 지식(KN_로 시작하는 id)의 내용을 고쳐 달라는 요청",
    "knowledge_delete": "기존 장애 지식(KN_로 시작하는 id)을 지워 달라는 요청",
    "doc_update": "사전 문서의 절 내용을 고쳐 달라는 요청",
    "doc_delete": "사전 문서의 절을 지워 달라는 요청",
    "out_of_scope": "서버·장애·코드·지식·문서와 관계없는 질문, 또는 코드·설정·인프라를 바꿔 달라는 요청",
}
KINDS = {
    "status": "최근 오류가 있는지, 서버·기능 상태가 어떤지 묻는 질문",
    "trace": "특정 요청 id를 추적하는 질문",
    "doc": "절차·설정·사용법·문서에 적힌 내용을 묻는 질문",
    "incident": "특정 장애·알림의 원인과 조치를 묻는 질문",
}


def choice(name: str, instructions: str, options: dict) -> dict:
    return {"type": "choice", "name": name, "instructions": instructions,
            "choices": [{"value": k, "description": v} for k, v in options.items()]}


ROUTE = [choice("intent", "운영자 메시지의 의도를 고르세요.", INTENTS),
         choice("kind", "질문의 종류를 고르세요.", KINDS),
         {"type": "predicate", "name": "needs_tools",
          "instructions": "미리 찾은 근거와 연결된 장애·알림 정보만으로는 답할 수 없어 로그·지표·코드 도구를 더 조회해야 하는가?"}]
SAME_CAUSE = [{"type": "predicate", "name": "same",
               "instructions": "예측 원인이 정답 원인과 같은 원인을 말하는가? 표현이 달라도 원인이 같으면 참, 원인을 정하지 못했거나 다른 원인이면 거짓."}]


class Decider:
    def __init__(self, settings, client=None):
        m = settings.section("model")
        self.model = m.get("decision", "gpt-6-luna")
        self.min = m.get("decision_min_confidence", 0.6)
        self.timeout = m.get("decision_timeout_s", 5)
        self.key, self._client = settings.openai_key, client

    def client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.key, timeout=self.timeout, max_retries=0)
        return self._client

    def ask(self, text: str, questions: list[dict]) -> dict | None:
        if not self.key and self._client is None:
            return None
        started = time.perf_counter()
        try:
            res = self.client().decisions.create(model=self.model, input=mask(text)[:8000], questions=questions)
            out = {a.name: {"choice": getattr(a, "choice", None), "confidence": getattr(a, "confidence", None),
                            "probability": getattr(a, "probability", None)} for a in res.answers}
        except Exception as exc:
            log.warning("decision failed: error=%s", type(exc).__name__)
            return None
        out["_ms"] = int((time.perf_counter() - started) * 1000)
        return out

    def route(self, text: str, context: str = "") -> dict | None:
        r = self.ask(f"{context}\n\n운영자 메시지: {text}" if context else text, ROUTE)
        if not r or "intent" not in r or "kind" not in r:
            return None
        sure = lambda a: a["choice"] if (a.get("confidence") or 0) >= self.min else None
        return {"intent": sure(r["intent"]), "kind": sure(r["kind"]), "needs_tools": (r.get("needs_tools") or {}).get("probability"),
                "raw": {k: v for k, v in r.items() if k != "_ms"}, "ms": r["_ms"]}

    def same_cause(self, expected: str, predicted: str) -> tuple[bool | None, str]:
        r = self.ask(f"정답 원인: {expected}\n예측 원인: {predicted}", SAME_CAUSE)
        p = ((r or {}).get("same") or {}).get("probability")
        if p is None:
            return None, "판단 실패"
        return p >= 0.5, f"판단 모델 확률 {p:.2f}"
