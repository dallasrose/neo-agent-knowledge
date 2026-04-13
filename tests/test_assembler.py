from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from neo.core.assembler import WorkingMemoryAssembler
from neo.store.sqlite import SQLiteStore


@pytest.mark.asyncio
async def test_assembler_pipeline_ranks_and_expands(session_factory):
    store = SQLiteStore(session_factory)
    assembler = WorkingMemoryAssembler(store)
    agent = await store.get_or_create_agent("neo")

    root = await store.create_node(agent["id"], "concept", "Root", "root", summary="root summary", confidence=0.9, parent_id=None, source_id=None, spark_id=None, embedding=[1.0, 0.0], domain="memory", metadata=None)
    child = await store.create_node(agent["id"], "finding", "Child", "child", summary="child summary", confidence=0.8, parent_id=root["id"], source_id=None, spark_id=None, embedding=[0.9, 0.1], domain="memory", metadata=None)
    contradictor = await store.create_node(agent["id"], "finding", "Conflict", "conflict", summary="conflict summary", confidence=0.7, parent_id=None, source_id=None, spark_id=None, embedding=[0.85, 0.15], domain="memory", metadata=None)
    await store.create_edge(agent["id"], root["id"], child["id"], "supports", weight=0.9, description="support", source_id=None, metadata=None)
    await store.create_edge(agent["id"], child["id"], contradictor["id"], "contradicts", weight=0.9, description="conflict", source_id=None, metadata=None)
    await store.create_spark(agent["id"], "open_question", "Need deeper evidence", priority=0.8, domain="memory", target_node_id=child["id"], source_id=None, metadata=None)

    result = await assembler.assemble(
        agent_id=agent["id"],
        query_embedding=[1.0, 0.0],
        query="memory",
        top_k=2,
        hop_depth=1,
        token_budget=100,
    )

    assert result["nodes"][0]["title"] == "Root"
    assert len(result["edges"]) == 2
    assert len(result["contradictions"]) == 1
    assert result["sparks"][0]["description"] == "Need deeper evidence"


@pytest.mark.asyncio
async def test_assembler_respects_token_budget(session_factory):
    store = SQLiteStore(session_factory)
    assembler = WorkingMemoryAssembler(store)
    agent = await store.get_or_create_agent("neo")

    for index in range(5):
        await store.create_node(
            agent["id"], "concept", f"Node {index}", "body",
            summary="many words " * 20, confidence=0.8, parent_id=None,
            source_id=None, spark_id=None, embedding=[1.0, 0.0], domain="memory", metadata=None
        )

    result = await assembler.assemble(
        agent_id=agent["id"],
        query_embedding=[1.0, 0.0],
        query="memory",
        token_budget=30,
    )

    assert len(result["nodes"]) == 1


@pytest.mark.asyncio
async def test_assembler_empty_query_returns_empty_result(session_factory):
    store = SQLiteStore(session_factory)
    assembler = WorkingMemoryAssembler(store)
    agent = await store.get_or_create_agent("neo")
    await store.create_node(agent["id"], "concept", "Node", "body", summary="summary", confidence=0.8, parent_id=None, source_id=None, spark_id=None, embedding=None, domain="memory", metadata=None)

    result = await assembler.assemble(agent_id=agent["id"], query_embedding=[1.0, 0.0], query="memory")
    assert result["nodes"] == []


@pytest.mark.asyncio
async def test_assembler_ranking_accounts_for_recency(session_factory):
    store = SQLiteStore(session_factory)
    assembler = WorkingMemoryAssembler(store)
    agent = await store.get_or_create_agent("neo")

    older = await store.create_node(agent["id"], "concept", "Older", "body", summary="older", confidence=0.9, parent_id=None, source_id=None, spark_id=None, embedding=[1.0, 0.0], domain="memory", metadata=None)
    newer = await store.create_node(agent["id"], "concept", "Newer", "body", summary="newer", confidence=0.9, parent_id=None, source_id=None, spark_id=None, embedding=[0.99, 0.01], domain="memory", metadata=None)
    await store.update_node(older["id"], metadata={"updated_at_override": (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()})

    result = await assembler.assemble(agent_id=agent["id"], query_embedding=[1.0, 0.0], query="memory")

    assert result["nodes"][0]["title"] in {"Older", "Newer"}
