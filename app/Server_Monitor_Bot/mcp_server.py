# 외부 클라이언트 전용 MCP 입구(내 PC의 Claude 등). 같은 읽기 전용 도구를 노출하고 서버에서는 실행하지 않는다.
from .config.settings import load


def build_ctx():
    from .runtime.context import Ctx
    from .store.embed import Embedder
    from .store.mysql import MySQLDB
    from .store.repos import Repos
    from .store.vector import VectorDB

    s = load()
    db = MySQLDB.from_url(s.db_url)
    vec = VectorDB(stage=s.stage, url=s.qdrant_url, api_key=s.qdrant_key, dim=s.section("model").get("embedding_dim", 1536))
    return Ctx(settings=s, repos=Repos(db, vec, Embedder(s.openai_key, s.section("model").get("embedding")), tuple(s.policy)))


def main() -> None:
    from mcp.server.fastmcp import FastMCP

    from .ops_agent.tools import raw, registry, work

    registry.bind(build_ctx())
    server = FastMCP("server-monitor-bot")
    for fn in (work.step_failures, work.llm_calls, work.dependency, work.resources, work.recent_deploy, work.knowledge_search,
               work.chat_search, work.docs_search, work.flags, raw.trace, raw.logs, raw.code_search, raw.code_read):
        server.tool()(fn)
    server.run()


if __name__ == "__main__":
    main()
