# 설치 스크립트 통합 확인: --check는 쓰지 않고, 빈 DB에 두 번 설치해도 같고, --reembed는 백업 후 새 차원으로 다시 넣는다. SERVER_BOT_TEST_DB_URL이 있을 때만.
import dataclasses
import hashlib
import os
from urllib.parse import urlparse

import pytest
from qdrant_client import QdrantClient

from app.Server_Monitor_Bot.config.settings import load
from app.Server_Monitor_Bot.install import counts, run
from app.Server_Monitor_Bot.runtime.context import Ctx
from app.Server_Monitor_Bot.store.mysql import MySQLDB
from app.Server_Monitor_Bot.store.repos import Repos
from app.Server_Monitor_Bot.store.vector import VectorDB

URL = os.environ.get("SERVER_BOT_TEST_DB_URL")
QDRANT = os.environ.get("SERVER_BOT_TEST_QDRANT_URL")
pytestmark = pytest.mark.skipif(not URL, reason="SERVER_BOT_TEST_DB_URL 없음")


class Embed:
    def __init__(self, dim):
        self.dim = dim

    def one(self, text):
        return self.many([text])[0]

    def many(self, texts):
        return [[b / 255 + 0.01 for b in hashlib.sha256(t.encode()).digest()[:self.dim]] for t in texts]


def ctx_for(db, client, dim, tmp_path):
    vec = VectorDB(stage="installtest", url=None, api_key=None, dim=dim, client=client)
    settings = dataclasses.replace(load(), buffer_dir=tmp_path)
    return Ctx(settings=settings, repos=Repos(db, vec, Embed(dim), tuple(settings.policy)), image_tag="install")


def test_install_check_idempotent_and_reembed(tmp_path):
    root = MySQLDB.from_url(URL)
    root.execute("DROP DATABASE IF EXISTS ai_installtest")
    root.execute("CREATE DATABASE ai_installtest CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
    db = MySQLDB.from_url(urlparse(URL)._replace(path="/ai_installtest").geturl())
    client = QdrantClient(url=QDRANT, api_key=os.environ.get("SERVER_BOT_TEST_QDRANT_KEY"), https=False) if QDRANT else QdrantClient(":memory:")
    ctx = ctx_for(db, client, 8, tmp_path)
    vec = ctx.repos.knowledge.vec
    for name in ("knowledge", "docs", "chat"):
        vec.drop(name)
    try:
        lines = []
        assert run(ctx, check=True, out=lines.append) == 0
        assert "MySQL 스키마: 없음" in lines and counts(vec) == {"knowledge": None, "docs": None, "chat": None}
        assert all("sk-" not in line for line in lines)
        assert run(ctx, out=lines.append) == 0 and "MySQL 스키마: 새로 적용" in lines
        first = counts(vec)
        assert first["knowledge"] == 23 and first["docs"] > 9 and first["chat"] == 0
        assert run(ctx, out=lines.append) == 0 and lines.count("MySQL 스키마: 이미 있음") == 1 and counts(vec) == first
        wide = ctx_for(db, client, 16, tmp_path)
        assert run(wide, redo=True, out=lines.append) == 0 and counts(vec) == first
        assert list(tmp_path.glob("reembed-*.json"))
        point = client.retrieve(vec.name("knowledge"), ids=[vec.point_id("knowledge:KN_OPENAI_RATE_LIMIT")], with_vectors=True)[0]
        assert len(point.vector) == 16
    finally:
        root.execute("DROP DATABASE IF EXISTS ai_installtest")
        for name in ("knowledge", "docs", "chat"):
            vec.drop(name)
