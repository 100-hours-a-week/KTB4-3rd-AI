# 하이브리드 재정렬: 벡터 후보를 질문과 겹치는 낱말(영숫자, 한글 두 글자, 후보 안 IDF 가중, 제목은 2배)로 다시 줄 세운다. score는 벡터 점수 그대로 두어 기준값 비교에는 쓰지 않는다.
import math
import re

HANGUL = re.compile(r"[가-힣]+")
WORD = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
TITLE_WEIGHT = 2.0


def tokens(text: str) -> set[str]:
    text, out = (text or "").lower(), set()
    for word in WORD.findall(text):
        if len(word) >= 2:
            out.add(word)
        out.update(part for part in re.split(r"[._-]", word) if len(part) >= 2)
    for run in HANGUL.findall(text):
        out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


def rerank(query: str, hits: list[dict], title_of, text_of, weight: float) -> list[dict]:
    if weight <= 0 or len(hits) < 2:
        return hits
    docs = [(tokens(title_of(h)), tokens(f"{title_of(h)} {text_of(h)}")) for h in hits]
    df: dict[str, int] = {}
    for _, words in docs:
        for t in words:
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log(1 + len(hits) / n) for t, n in df.items()}
    asked = [t for t in tokens(query) if t in idf]
    if not asked:
        return hits
    total = sum(idf[t] for t in asked) * TITLE_WEIGHT

    def lexical(i: int) -> float:
        title, words = docs[i]
        return sum(idf[t] * (TITLE_WEIGHT if t in title else 1.0 if t in words else 0.0) for t in asked) / total

    order = sorted(range(len(hits)), key=lambda i: -((hits[i].get("score") or 0.0) + weight * lexical(i)))
    return [hits[i] for i in order]


if __name__ == "__main__":
    hits = [{"title": "OpenAI 키 무효", "text": "401 Unauthorized", "score": 0.52}, {"title": "키 주입 누락", "text": "key missing", "score": 0.53}]
    top = rerank("unauthorized 에러", hits, lambda h: h["title"], lambda h: h["text"], 0.3)
    assert top[0]["title"] == "OpenAI 키 무효" and top[0]["score"] == 0.52
    assert rerank("x", hits, lambda h: h["title"], lambda h: h["text"], 0) == hits
    print("ok")
