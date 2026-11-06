# Qdrant 래퍼. 컬렉션은 <stage>_knowledge·<stage>_docs(원본), <stage>_chat(MySQL 원문의 색인).
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (Distance, FieldCondition, Filter, MatchAny, MatchValue, PayloadSchemaType, PointIdsList,
                                  PointStruct, Range, VectorParams)

NAMESPACE = uuid.UUID("6f1c2a5e-0b8d-4c7a-9a3e-2d4b5c6e7f80")
INDEXES = {
    "knowledge": {"status": PayloadSchemaType.KEYWORD, "features": PayloadSchemaType.KEYWORD,
                  "step_ids": PayloadSchemaType.KEYWORD, "knowledge_id": PayloadSchemaType.KEYWORD},
    "docs": {"doc_id": PayloadSchemaType.KEYWORD, "origin": PayloadSchemaType.KEYWORD, "status": PayloadSchemaType.KEYWORD},
    "chat": {"incident_id": PayloadSchemaType.KEYWORD, "ts": PayloadSchemaType.INTEGER},
}
COLLECTIONS = ("knowledge", "docs", "chat")


class VectorDB:
    def __init__(self, *, stage: str, url: str | None, api_key: str | None, dim: int = 1536, client: QdrantClient | None = None):
        if client is None:
            if not url:
                if stage != "local":
                    raise RuntimeError("AI_QDRANT_URL 누락: local 외 스테이지는 폴백 금지")
                url = "http://localhost:6333"
            client = QdrantClient(url=url, api_key=api_key, timeout=10)
        self.c = client
        self.stage, self.dim = stage, dim

    def name(self, collection: str) -> str:
        return f"{self.stage}_{collection}"

    @staticmethod
    def point_id(doc_id: str) -> str:
        return str(uuid.uuid5(NAMESPACE, doc_id))

    def ensure(self, collection: str) -> None:
        name = self.name(collection)
        if not self.c.collection_exists(name):
            self.c.create_collection(name, vectors_config=VectorParams(size=self.dim, distance=Distance.COSINE))
            for field, schema in INDEXES.get(collection, {}).items():
                self.c.create_payload_index(name, field_name=field, field_schema=schema)

    def call(self, collection: str, fn):
        try:
            return fn(self.name(collection))
        except Exception:
            if self.c.collection_exists(self.name(collection)):
                raise
            self.ensure(collection)
            return fn(self.name(collection))

    def count(self, collection: str) -> int:
        return self.call(collection, lambda name: self.c.count(name, exact=True).count)

    def upsert(self, collection: str, doc_id: str, vector: list[float], payload: dict) -> None:
        self.upsert_many(collection, [(doc_id, vector, payload)])

    def upsert_many(self, collection: str, points: list[tuple[str, list[float], dict]]) -> None:
        if points:
            self.call(collection, lambda name: self.c.upsert(name, points=[PointStruct(id=self.point_id(d), vector=v, payload=p)
                                                                          for d, v, p in points]))

    def set_payload(self, collection: str, doc_id: str, payload: dict) -> None:
        self.call(collection, lambda name: self.c.set_payload(name, payload=payload, points=[self.point_id(doc_id)]))

    def delete(self, collection: str, doc_id: str) -> None:
        self.delete_ids(collection, [doc_id])

    def delete_ids(self, collection: str, doc_ids: list[str]) -> None:
        if doc_ids:
            self.call(collection, lambda name: self.c.delete(name, points_selector=PointIdsList(points=[self.point_id(d) for d in doc_ids])))

    def drop(self, collection: str) -> None:
        if self.c.collection_exists(self.name(collection)):
            self.c.delete_collection(self.name(collection))

    def delete_older(self, collection: str, field: str, before: int) -> None:
        self.call(collection, lambda name: self.c.delete(name, points_selector=Filter(must=[FieldCondition(key=field, range=Range(lt=before))])))

    @staticmethod
    def where(equals: dict | None = None, any_of: dict[str, list] | None = None, none_of: dict[str, list] | None = None) -> Filter | None:
        must = [FieldCondition(key=k, match=MatchValue(value=v)) for k, v in (equals or {}).items() if v is not None]
        must += [FieldCondition(key=k, match=MatchAny(any=vs)) for k, vs in (any_of or {}).items() if vs]
        must_not = [FieldCondition(key=k, match=MatchAny(any=vs)) for k, vs in (none_of or {}).items() if vs]
        return Filter(must=must or None, must_not=must_not or None) if (must or must_not) else None

    def search(self, collection: str, vector: list[float], *, limit: int = 5, equals: dict | None = None,
               any_of: dict[str, list] | None = None, none_of: dict[str, list] | None = None) -> list[dict]:
        flt = self.where(equals, any_of, none_of)
        res = self.call(collection, lambda name: self.c.query_points(name, query=vector, query_filter=flt, limit=limit))
        return [{"score": p.score, **(p.payload or {})} for p in res.points]

    def scroll(self, collection: str, equals: dict | None = None, none_of: dict[str, list] | None = None) -> list[dict]:
        flt, out, offset = self.where(equals, none_of=none_of), [], None
        while True:
            points, offset = self.call(collection, lambda name: self.c.scroll(name, scroll_filter=flt, limit=256, offset=offset,
                                                                              with_payload=True))
            out += [p.payload or {} for p in points]
            if offset is None:
                return out

    def vectors(self, collection: str, doc_ids: list[str]) -> dict[str, list[float]]:
        ids = {self.point_id(d): d for d in doc_ids}
        points = self.call(collection, lambda name: self.c.retrieve(name, ids=list(ids), with_payload=False, with_vectors=True))
        return {ids[str(p.id)]: p.vector for p in points}

    def payloads(self, collection: str, doc_ids: list[str]) -> dict[str, dict]:
        ids = {self.point_id(d): d for d in doc_ids}
        points = self.call(collection, lambda name: self.c.retrieve(name, ids=list(ids), with_payload=True, with_vectors=False))
        return {ids[str(p.id)]: p.payload or {} for p in points}
