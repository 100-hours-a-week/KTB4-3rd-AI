# OpenAI 임베딩(text-embedding-3-small, 1536차원). httpx로 /v1/embeddings를 직접 부른다.
import httpx

URL = "https://api.openai.com/v1/embeddings"


class Embedder:
    def __init__(self, api_key: str | None, model: str = "text-embedding-3-small", timeout: float = 15.0):
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def many(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise RuntimeError("embedding key missing")
        res = httpx.post(URL, headers={"Authorization": f"Bearer {self.api_key}"},
                         json={"model": self.model, "input": [t[:8000] for t in texts]}, timeout=self.timeout)
        res.raise_for_status()
        return [row["embedding"] for row in sorted(res.json()["data"], key=lambda r: r["index"])]

    def one(self, text: str) -> list[float]:
        return self.many([text])[0]
