# Qdrant 래퍼. 컬렉션은 <stage>_knowledge, <stage>_chat. 원문은 MySQL이고 여기는 검색 인덱스만 둔다.
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (Distance, FieldCondition, Filter, MatchAny, MatchValue, PayloadSchemaType, PointIdsList,
                                  PointStruct, Range, VectorParams)

NAMESPACE = uuid.UUID("6f1c2a5e-0b8d-4c7a-9a3e-2d4b5c6e7f80")
INDEXES = {
    "knowledge": {"status": PayloadSchemaType.KEYWORD, "features": PayloadSchemaType.KEYWORD,
                  "step_ids": PayloadSchemaType.KEYWORD, "knowledge_id": PayloadSchemaType.KEYWORD},
    "chat": {"incident_id": PayloadSchemaType.KEYWORD, "ts": PayloadSchemaType.INTEGER},
}


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

    def upsert(self, collection: str, doc_id: str, vector: list[float], payload: dict) -> None:
        self.c.upsert(self.name(collection), points=[PointStruct(id=self.point_id(doc_id), vector=vector, payload=payload)])

    def set_payload(self, collection: str, doc_id: str, payload: dict) -> None:
        self.c.set_payload(self.name(collection), payload=payload, points=[self.point_id(doc_id)])

    def delete(self, collection: str, doc_id: str) -> None:
        self.c.delete(self.name(collection), points_selector=PointIdsList(points=[self.point_id(doc_id)]))

    def delete_older(self, collection: str, field: str, before: int) -> None:
        self.c.delete(self.name(collection), points_selector=Filter(must=[FieldCondition(key=field, range=Range(lt=before))]))

    def search(self, collection: str, vector: list[float], *, limit: int = 5, equals: dict | None = None,
               any_of: dict[str, list] | None = None, none_of: dict[str, list] | None = None) -> list[dict]:
        must = [FieldCondition(key=k, match=MatchValue(value=v)) for k, v in (equals or {}).items() if v is not None]
        must += [FieldCondition(key=k, match=MatchAny(any=vs)) for k, vs in (any_of or {}).items() if vs]
        must_not = [FieldCondition(key=k, match=MatchAny(any=vs)) for k, vs in (none_of or {}).items() if vs]
        flt = Filter(must=must or None, must_not=must_not or None) if (must or must_not) else None
        res = self.c.query_points(self.name(collection), query=vector, query_filter=flt, limit=limit)
        return [{"score": p.score, **(p.payload or {})} for p in res.points]

    def payloads(self, collection: str, doc_ids: list[str]) -> dict[str, dict]:
        ids = {self.point_id(d): d for d in doc_ids}
        points = self.c.retrieve(self.name(collection), ids=list(ids), with_payload=True, with_vectors=False)
        return {ids[str(p.id)]: p.payload or {} for p in points}
