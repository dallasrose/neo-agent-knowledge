from __future__ import annotations

from datetime import datetime, timezone

import pytest

from neo.core.api import NeoAPI
from neo.core.sparks import SparkGenerator
from neo.store.sqlite import SQLiteStore


class StubEmbeddingClient:
    async def embed_text(self, title: str, content: str) -> list[float]:
        text = f"{title} {content}".lower()
        if "memory" in text:
            return [1.0, 0.0, 0.0]
        if "graph" in text:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


class StubSparkLLM:
    async def generate(self, node, context):
        return [{"spark_type": "open_question", "description": f"question from {node['title']}"}]


@pytest.mark.asyncio
async def test_store_node_creates_embedding_and_returns_expected_fields(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient())

    result = await api.store_node(
        agent_id=agent["id"],
        node_type="concept",
        title="Semantic Memory",
        content="Memory stores structured knowledge",
        generate_sparks=False,
    )

    stored = await store.get_node(result["id"])
    assert result["title"] == "Semantic Memory"
    assert stored["embedding"] == [1.0, 0.0, 0.0]
    assert result["spark_generation"] == "skipped"


@pytest.mark.asyncio
async def test_store_node_defaults_to_agent_root_parent(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    root = await store.create_node(
        agent["id"],
        "concept",
        "Neo",
        "Agent root",
        summary="root",
        confidence=1.0,
        parent_id=None,
        source_id=None,
        spark_id=None,
        embedding=None,
        domain=None,
        metadata={"system": True, "role": "agent_root"},
    )
    await store.update_agent(agent["id"], config={"root_node_id": root["id"]})
    api = NeoAPI(store, embedding_client=StubEmbeddingClient())

    result = await api.store_node(
        agent_id=agent["id"],
        node_type="finding",
        title="Default Parent",
        content="Nodes created without a parent land under the agent root.",
        generate_sparks=False,
    )

    stored = await store.get_node(result["id"])
    assert stored["parent_id"] == root["id"]


@pytest.mark.asyncio
async def test_link_nodes_creates_contradiction_spark(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient())

    first = await store.create_node(agent["id"], "finding", "A", "memory", summary="a", confidence=0.8, parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="memory", metadata=None)
    second = await store.create_node(agent["id"], "finding", "B", "graph", summary="b", confidence=0.8, parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="memory", metadata=None)

    edge = await api.link_nodes(
        agent_id=agent["id"],
        from_node_id=first["id"],
        to_node_id=second["id"],
        edge_type="contradicts",
        description="Conflict",
        weight=0.9,
    )
    sparks = await store.get_sparks(agent["id"], status="active")

    assert edge["edge_type"] == "contradicts"
    assert sparks[0]["spark_type"] == "contradiction"


@pytest.mark.asyncio
async def test_get_node_returns_direct_read_payload(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient())

    parent = await store.create_node(agent["id"], "concept", "Agents", "memory", summary="agents", confidence=0.9, parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="agents", metadata=None)
    child = await store.create_node(agent["id"], "concept", "Tool Use", "memory", summary="tool use", confidence=0.8, parent_id=parent["id"], source_id=None, spark_id=None, embedding=[1.0], domain="agents", metadata=None)
    await store.create_edge(agent["id"], parent["id"], child["id"], "example_of", weight=0.8, description="child domain", source_id=None, metadata=None)

    payload = await api.get_node(node_id=child["id"])

    assert payload["node"]["title"] == "Tool Use"
    assert payload["ancestors"][0]["title"] == "Agents"
    assert payload["edges"][0]["edge_type"] == "example_of"


@pytest.mark.asyncio
async def test_get_branch_returns_root_and_descendants(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient())

    root = await store.create_node(agent["id"], "concept", "Agents", "memory", summary="agents", confidence=0.9, parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="agents", metadata=None)
    child = await store.create_node(agent["id"], "concept", "Planning", "memory", summary="planning", confidence=0.8, parent_id=root["id"], source_id=None, spark_id=None, embedding=[1.0], domain="agents", metadata=None)
    grandchild = await store.create_node(agent["id"], "finding", "Planning heuristic", "memory", summary="heuristic", confidence=0.8, parent_id=child["id"], source_id=None, spark_id=None, embedding=[1.0], domain="agents", metadata=None)
    await store.create_edge(agent["id"], child["id"], grandchild["id"], "supports", weight=0.8, description="detail", source_id=None, metadata=None)

    branch = await api.get_branch(root_node_id=root["id"], max_depth=2)

    titles = {node["title"] for node in branch["nodes"]}
    assert branch["root"]["title"] == "Agents"
    assert titles == {"Agents", "Planning", "Planning heuristic"}
    assert branch["edges"][0]["edge_type"] == "supports"


@pytest.mark.asyncio
async def test_find_node_by_title_returns_ranked_matches_and_ambiguity(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient())

    await store.create_node(agent["id"], "concept", "Agents", "memory", summary="primary agents root", confidence=0.95, parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="agents", metadata=None)
    await store.create_node(agent["id"], "concept", "Agents", "memory", summary="secondary agents root", confidence=0.7, parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="operations", metadata=None)
    await store.create_node(agent["id"], "concept", "Agent Workflow", "memory", summary="workflow", confidence=0.85, parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="agents", metadata=None)

    payload = await api.find_node_by_title(agent_id=agent["id"], title="Agents", exact=True, limit=5)

    assert payload["count"] == 2
    assert payload["ambiguous"] is True
    assert payload["selected_match"]["summary"] == "primary agents root"
    assert [match["summary"] for match in payload["matches"]] == ["primary agents root", "secondary agents root"]


@pytest.mark.asyncio
async def test_find_node_by_title_supports_partial_and_domain_filters(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient())

    await store.create_node(agent["id"], "concept", "Agent Workflow", "memory", summary="workflow", confidence=0.8, parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="agents", metadata=None)
    await store.create_node(agent["id"], "concept", "Agent Evaluations", "memory", summary="evals", confidence=0.8, parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="agents", metadata=None)
    await store.create_node(agent["id"], "concept", "Memory Workflow", "memory", summary="memory workflow", confidence=0.8, parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="memory", metadata=None)

    payload = await api.find_node_by_title(agent_id=agent["id"], title="agent", exact=False, domain="agents", limit=10)

    assert payload["count"] == 2
    assert {match["title"] for match in payload["matches"]} == {"Agent Workflow", "Agent Evaluations"}


@pytest.mark.asyncio
async def test_update_node_reembeds_content_change(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient())

    node = await store.create_node(agent["id"], "concept", "Topic", "graph", summary="g", confidence=0.6, parent_id=None, source_id=None, spark_id=None, embedding=[0.0, 1.0, 0.0], domain="memory", metadata=None)
    updated = await api.update_node(node_id=node["id"], content="memory", summary="m")

    assert updated["embedding"] == [1.0, 0.0, 0.0]
    assert updated["summary"] == "m"


@pytest.mark.asyncio
async def test_update_node_can_change_parent_and_reject_cycles(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient())

    root = await store.create_node(agent["id"], "concept", "Root", "root", summary="root", confidence=1.0, parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="memory", metadata=None)
    child = await store.create_node(agent["id"], "concept", "Child", "child", summary="child", confidence=0.8, parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="memory", metadata=None)
    grandchild = await store.create_node(agent["id"], "finding", "Grandchild", "grandchild", summary="grandchild", confidence=0.8, parent_id=child["id"], source_id=None, spark_id=None, embedding=[1.0], domain="memory", metadata=None)

    updated = await api.update_node(node_id=child["id"], parent_id=root["id"])
    assert updated["parent_id"] == root["id"]

    with pytest.raises(ValueError, match="descendants"):
        await api.update_node(node_id=child["id"], parent_id=grandchild["id"])


@pytest.mark.asyncio
async def test_search_knowledge_returns_ranked_results(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient())

    first = await store.create_node(agent["id"], "concept", "Semantic Memory", "memory", summary="memory first", confidence=0.9, parent_id=None, source_id=None, spark_id=None, embedding=[1.0, 0.0, 0.0], domain="memory", metadata=None)
    second = await store.create_node(agent["id"], "concept", "Graph Search", "graph", summary="graph second", confidence=0.9, parent_id=None, source_id=None, spark_id=None, embedding=[0.0, 1.0, 0.0], domain="memory", metadata=None)
    await store.create_edge(agent["id"], first["id"], second["id"], "supports", weight=0.8, description="link", source_id=None, metadata=None)

    results = await api.search_knowledge(agent_id=agent["id"], query="memory retrieval", top_k=2, token_budget=50)

    assert results["nodes"][0]["title"] == "Semantic Memory"
    assert results["total_candidates"] >= 2


@pytest.mark.asyncio
async def test_get_sparks_returns_prioritized_active_items(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient())

    await store.create_spark(agent["id"], "open_question", "low", priority=0.2, domain="memory", target_node_id=None, source_id=None, metadata=None)
    await store.create_spark(agent["id"], "contradiction", "high", priority=0.9, domain="memory", target_node_id=None, source_id=None, metadata=None)

    sparks = await api.get_sparks(agent_id=agent["id"], limit=2)
    assert [spark["description"] for spark in sparks] == ["high", "low"]


@pytest.mark.asyncio
async def test_resolve_spark_links_to_nodes(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient())

    node = await store.create_node(agent["id"], "answer", "Answer", "memory", summary="a", confidence=0.8, parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="memory", metadata=None)
    spark = await store.create_spark(agent["id"], "open_question", "Investigate", priority=0.5, domain="memory", target_node_id=None, source_id=None, metadata=None)

    resolved = await api.resolve_spark(spark_id=spark["id"], node_ids=[node["id"]], notes="resolved")

    assert resolved["status"] == "resolved"
    assert resolved["resolved_node_id"] == node["id"]


@pytest.mark.asyncio
async def test_get_activity_summary_returns_structure(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient())

    await api.store_node(
        agent_id=agent["id"],
        node_type="concept",
        title="Semantic Memory",
        content="Memory stores structured knowledge",
        generate_sparks=False,
    )

    summary = await api.get_activity_summary(agent_id=agent["id"], since=datetime.now(timezone.utc).replace(microsecond=0).isoformat())

    assert "period" in summary
    assert "counts" in summary
    assert "recent_nodes" in summary


@pytest.mark.asyncio
async def test_configure_agent_stores_suggested_sources_in_agent_config(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient())

    result = await api.configure_agent(
        agent_id=agent["id"],
        specialty="Research agent memory.",
        suggested_sources=["Neo docs", "Agent interviews"],
        trigger_discovery=False,
    )
    info = await api.get_agent_info(agent_id=agent["id"])

    assert result["suggested_sources"] == ["Neo docs", "Agent interviews"]
    assert info["config"]["suggested_sources"] == ["Neo docs", "Agent interviews"]
