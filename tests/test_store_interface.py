from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from neo.store.sqlite import SQLiteStore


@pytest.mark.asyncio
async def test_store_crud_flow(session_factory):
    store = SQLiteStore(session_factory)

    agent = await store.get_or_create_agent("neo", specialty="semantic memory")
    source = await store.create_source(agent["id"], "document", "Spec", "/tmp/spec.md")
    spark = await store.create_spark(
        agent["id"],
        "open_question",
        "Need more evidence",
        priority=0.6,
        domain="memory",
        target_node_id=None,
        source_id=source["id"],
        metadata={"origin": "test"},
    )
    node = await store.create_node(
        agent["id"],
        "concept",
        "Neo",
        "Neo is a semantic memory system",
        summary="neo = semantic memory system",
        confidence=0.8,
        parent_id=None,
        source_id=source["id"],
        spark_id=spark["id"],
        embedding=[1.0, 0.0, 0.0],
        domain="memory",
        metadata={"stage": 1},
    )

    fetched_agent = await store.get_agent(agent["id"])
    fetched_node = await store.get_node(node["id"])
    fetched_source = await store.get_source(source["id"])

    assert fetched_agent["name"] == "neo"
    assert fetched_node["title"] == "Neo"
    assert fetched_node["embedding"] == [1.0, 0.0, 0.0]
    assert fetched_source["title"] == "Spec"


@pytest.mark.asyncio
async def test_list_agents_returns_all_agents_by_name(session_factory):
    store = SQLiteStore(session_factory)
    await store.get_or_create_agent("zeta")
    await store.get_or_create_agent("atlas")

    agents = await store.list_agents()

    assert [agent["name"] for agent in agents] == ["atlas", "zeta"]


@pytest.mark.asyncio
async def test_vector_search_returns_most_similar_node(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")

    await store.create_node(
        agent["id"],
        "concept",
        "Aligned",
        "best match",
        summary="best",
        confidence=0.9,
        parent_id=None,
        source_id=None,
        spark_id=None,
        embedding=[1.0, 0.0, 0.0],
        domain="memory",
        metadata=None,
    )
    await store.create_node(
        agent["id"],
        "concept",
        "Orthogonal",
        "less match",
        summary="other",
        confidence=0.9,
        parent_id=None,
        source_id=None,
        spark_id=None,
        embedding=[0.0, 1.0, 0.0],
        domain="memory",
        metadata=None,
    )

    results = await store.vector_search(agent["id"], [0.9, 0.1, 0.0], top_k=2)

    assert results[0]["title"] == "Aligned"
    assert results[0]["similarity"] > results[1]["similarity"]


@pytest.mark.asyncio
async def test_graph_traversal_and_hierarchy_queries(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")

    root = await store.create_node(
        agent["id"], "concept", "Root", "r", summary="r", confidence=0.8,
        parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="memory", metadata=None
    )
    child = await store.create_node(
        agent["id"], "concept", "Child", "c", summary="c", confidence=0.8,
        parent_id=root["id"], source_id=None, spark_id=None, embedding=[0.5], domain="memory", metadata=None
    )
    leaf = await store.create_node(
        agent["id"], "finding", "Leaf", "l", summary="l", confidence=0.8,
        parent_id=child["id"], source_id=None, spark_id=None, embedding=[0.25], domain="memory", metadata=None
    )
    peer = await store.create_node(
        agent["id"], "concept", "Peer", "p", summary="p", confidence=0.8,
        parent_id=root["id"], source_id=None, spark_id=None, embedding=[0.75], domain="memory", metadata=None
    )

    await store.create_edge(agent["id"], root["id"], child["id"], "supports", weight=0.9, description="r->c", source_id=None, metadata=None)
    await store.create_edge(agent["id"], child["id"], leaf["id"], "extends", weight=0.9, description="c->l", source_id=None, metadata=None)
    await store.create_edge(agent["id"], child["id"], peer["id"], "connects", weight=0.6, description="c->p", source_id=None, metadata=None)

    neighborhood = await store.get_neighborhood(child["id"], depth=1, min_weight=0.5)
    ancestors = await store.get_ancestors(leaf["id"], max_depth=5)
    descendants = await store.get_descendants(root["id"], max_depth=5)

    neighborhood_titles = {node["title"] for node in neighborhood["nodes"]}
    assert neighborhood_titles == {"Root", "Child", "Leaf", "Peer"}
    assert [node["title"] for node in ancestors] == ["Child", "Root"]
    assert {node["title"] for node in descendants} == {"Child", "Leaf", "Peer"}


@pytest.mark.asyncio
async def test_activity_and_spark_resolution(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")

    node = await store.create_node(
        agent["id"], "question", "Question", "q", summary="q", confidence=0.7,
        parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="research", metadata=None
    )
    answer = await store.create_node(
        agent["id"], "answer", "Answer", "a", summary="a", confidence=0.9,
        parent_id=node["id"], source_id=None, spark_id=None, embedding=[1.0], domain="research", metadata=None
    )
    spark = await store.create_spark(
        agent["id"], "open_question", "Investigate question", priority=0.8,
        domain="research", target_node_id=node["id"], source_id=None, metadata=None
    )
    await store.create_edge(agent["id"], node["id"], answer["id"], "resolves", weight=0.95, description="answer resolves question", source_id=None, metadata=None)
    await store.resolve_spark(spark["id"], [answer["id"]])
    await store.update_node(answer["id"], content="updated", summary="updated", confidence=0.95)

    activity = await store.get_activity(agent["id"], datetime.now(timezone.utc) - timedelta(days=1))

    assert activity["counts"]["nodes_created"] == 2
    assert activity["counts"]["nodes_updated"] == 1
    assert activity["counts"]["edges_created"] == 1
    assert activity["counts"]["sparks_generated"] == 1
    assert activity["counts"]["sparks_resolved"] == 1


@pytest.mark.asyncio
async def test_duplicate_edge_constraint(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    left = await store.create_node(
        agent["id"], "concept", "Left", "l", summary="l", confidence=0.8,
        parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain=None, metadata=None
    )
    right = await store.create_node(
        agent["id"], "concept", "Right", "r", summary="r", confidence=0.8,
        parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain=None, metadata=None
    )

    await store.create_edge(agent["id"], left["id"], right["id"], "connects", weight=0.5, description="first", source_id=None, metadata=None)
    with pytest.raises(IntegrityError):
        await store.create_edge(agent["id"], left["id"], right["id"], "connects", weight=0.5, description="dup", source_id=None, metadata=None)


@pytest.mark.asyncio
async def test_update_edge_changes_type_description_weight_and_metadata(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    left = await store.create_node(
        agent["id"], "concept", "Left", "l", summary="l", confidence=0.8,
        parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain=None, metadata=None
    )
    right = await store.create_node(
        agent["id"], "concept", "Right", "r", summary="r", confidence=0.8,
        parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain=None, metadata=None
    )
    edge = await store.create_edge(
        agent["id"], left["id"], right["id"], "connects",
        weight=0.5, description="first", source_id=None, metadata={"generated_by": "auto_link"}
    )

    updated = await store.update_edge(
        edge["id"],
        edge_type="supports",
        weight=0.9,
        description="strong support",
        metadata={"judge_confidence": 0.9},
    )

    assert updated["edge_type"] == "supports"
    assert updated["weight"] == 0.9
    assert updated["description"] == "strong support"
    assert updated["metadata"] == {"generated_by": "auto_link", "judge_confidence": 0.9}


@pytest.mark.asyncio
async def test_count_nodes_since_and_unconsolidated(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    older_than = datetime.now(timezone.utc) - timedelta(days=1)

    node = await store.create_node(
        agent["id"], "concept", "Node", "n", summary="n", confidence=0.5,
        parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="memory", metadata=None
    )
    await store.update_node(node["id"], consolidation_version=1)

    recent_count = await store.count_nodes_since(agent["id"], older_than)
    unconsolidated = await store.get_unconsolidated_nodes(agent["id"], since_version=1)

    assert recent_count == 1
    assert unconsolidated[0]["id"] == node["id"]
