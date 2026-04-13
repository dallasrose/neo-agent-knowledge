from __future__ import annotations

import json

import pytest

from neo.mcp import server as mcp_server


class StubEmbeddingClient:
    async def embed_text(self, title: str, content: str) -> list[float]:
        text = f"{title} {content}".lower()
        if "memory" in text:
            return [1.0, 0.0]
        return [0.0, 1.0]


@pytest.mark.asyncio
async def test_mcp_tools_round_trip(session_factory, monkeypatch):
    from neo.core.api import NeoAPI
    from neo.store.sqlite import SQLiteStore

    api = NeoAPI(SQLiteStore(session_factory), embedding_client=StubEmbeddingClient())

    async def fake_get_api():
        agent = await api.store.get_or_create_agent("default")
        await api.store.get_or_create_agent("default")
        return api

    monkeypatch.setattr(mcp_server, "get_api", fake_get_api)

    node_result = json.loads(await mcp_server.store_node_tool(node_type="concept", title="Semantic Memory", content="memory"))
    node_read = json.loads(await mcp_server.get_node_tool(node_id=node_result["id"]))
    branch_read = json.loads(await mcp_server.get_branch_tool(root_node_id=node_result["id"], max_depth=1))
    title_lookup = json.loads(await mcp_server.find_node_by_title_tool(title="Semantic Memory"))
    search_result = json.loads(await mcp_server.search_knowledge_tool(query="memory"))
    activity_result = json.loads(await mcp_server.get_activity_summary_tool())

    assert node_result["title"] == "Semantic Memory"
    assert node_read["node"]["title"] == "Semantic Memory"
    assert branch_read["root"]["title"] == "Semantic Memory"
    assert title_lookup["selected_match"]["title"] == "Semantic Memory"
    assert search_result["nodes"][0]["title"] == "Semantic Memory"
    assert "counts" in activity_result


@pytest.mark.asyncio
async def test_mcp_tools_validate_bad_inputs(session_factory, monkeypatch):
    from neo.core.api import NeoAPI
    from neo.store.sqlite import SQLiteStore

    api = NeoAPI(SQLiteStore(session_factory), embedding_client=StubEmbeddingClient())

    async def fake_get_api():
        await api.store.get_or_create_agent("default")
        return api

    monkeypatch.setattr(mcp_server, "get_api", fake_get_api)

    with pytest.raises(ValueError):
        await mcp_server.store_node_tool(node_type="bad", title="x", content="y")
