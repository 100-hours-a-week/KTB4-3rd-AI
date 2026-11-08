# 하이브리드 재정렬: 낱말 분리, 점수는 벡터 값 유지, 기준값은 벡터 점수로만, 지식 검색 텍스트 보강과 버전이 다른 지식 재임베딩.
from app.Server_Monitor_Bot.store import rerank as rr
from app.Server_Monitor_Bot.store.repos import knowledge as kn


def test_tokens_split_codes_and_korean():
    t = rr.tokens("luna_call 429 이미지다운로드")
    assert {"luna_call", "luna", "call", "429", "이미", "다운"} <= t


def test_rerank_reorders_without_touching_scores():
    hits = [{"title": "키 주입 누락", "text": "key missing", "score": 0.53}, {"title": "OpenAI 키 무효", "text": "401 Unauthorized", "score": 0.52}]
    top = rr.rerank("unauthorized 에러", hits, lambda h: h["title"], lambda h: h["text"], 0.3)
    assert [h["score"] for h in top] == [0.52, 0.53]
    assert rr.rerank("unauthorized", hits, lambda h: h["title"], lambda h: h["text"], 0) == hits


def test_embed_text_names_codes():
    text = kn.embed_text({"title": "t", "symptom": "s", "cause": "c", "match": {"step_id": ["image_fetch"], "status": [403], "reason": ["redirect"]}})
    assert "Forbidden" in text and "이미지 다운로드" in text and "리다이렉트" in text


class Vec:
    def __init__(self, rows):
        self.rows, self.upserts = rows, []

    def search(self, collection, vector, limit, equals=None, any_of=None, none_of=None):
        return [r for r in self.rows if r["id"] not in (none_of or {}).get("knowledge_id", [])][:limit]

    def scroll(self, collection, equals=None, none_of=None):
        return self.rows

    def upsert(self, collection, doc_id, vector, payload):
        self.upserts.append(payload)
        self.rows = [payload if r["id"] == payload["id"] else r for r in self.rows]


class Embed:
    def one(self, text):
        return [0.0]

    def many(self, texts):
        return [[0.0] for _ in texts]


def item(kid, title, score, embed_v=None):
    return {"id": kid, "knowledge_id": kid, "title": title, "symptom": "", "cause": "", "match": {}, "status": "active", "score": score,
            "embed_v": embed_v}


def test_search_gates_on_vector_score_and_reranks():
    vec = Vec([item("A", "키 주입 누락", 0.53), item("B", "OpenAI 키 무효 unauthorized", 0.52), item("C", "unauthorized 다른 것", 0.2)])
    k = kn.Knowledge(None, vec, Embed())
    assert [h["id"] for h in k.search("unauthorized", limit=2, min_score=0.4, lexical=0.3)] == ["B", "A"]
    assert [h["id"] for h in k.search("unauthorized", limit=2, min_score=0.4)] == ["A", "B"]


def test_reembed_only_stale_items_once():
    vec = Vec([item("A", "a", 0.5, embed_v=1), item("B", "b", 0.5, embed_v=kn.EMBED_V)])
    k = kn.Knowledge(None, vec, Embed())
    assert k.reembed_stale() == 1 and vec.upserts[0]["id"] == "A" and vec.upserts[0]["embed_v"] == kn.EMBED_V
    assert k.reembed_stale() == 0
