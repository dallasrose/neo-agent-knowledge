from __future__ import annotations

import pytest

from neo.core.consolidation import ConsolidationEngine
from neo.core.sparks import SparkGenerator
from neo.store.sqlite import SQLiteStore


class StubPerNodeModel:
    async def refine(self, node, neighborhood):
        return {
            "summary": f"{node['summary']} refined",
            "confidence": min(node["confidence"] + 0.1, 1.0),
            "content": node["content"],
        }


class StubCrossNodeModel:
    async def synthesize(self, domain, nodes):
        if not nodes:
            return {"synthesis_nodes": [], "sparks": []}
        return {
            "synthesis_nodes": [
                {
                    "title": f"{domain.title()} synthesis",
                    "content": "Combined insight",
                    "summary": "combined insight",
                    "confidence": 0.85,
                    "embedding": [1.0],
                    "source_node_ids": [node["id"] for node in nodes],
                }
            ],
            "sparks": [
                {
                    "spark_type": "thin_domain",
                    "description": f"Explore {domain} further",
                    "priority": 0.4,
                    "domain": domain,
                }
            ],
        }


@pytest.mark.asyncio
async def test_consolidation_updates_nodes_and_creates_synthesis(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    node = await store.create_node(agent["id"], "concept", "Neo", "memory", summary="neo", confidence=0.6, parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="memory", metadata=None)
    engine = ConsolidationEngine(
        store,
        per_node_model=StubPerNodeModel(),
        cross_node_model=StubCrossNodeModel(),
        spark_generator=SparkGenerator(store),
    )

    result = await engine.run(agent["id"])
    updated = await store.get_node(node["id"])
    nodes = await store.get_nodes_by_agent(agent["id"])
    sparks = await store.get_sparks(agent["id"], status="active")

    assert result["nodes_processed"] == 1
    assert result["syntheses_created"] == 1
    assert result["sparks_generated"] == 1
    assert updated["summary"].endswith("refined")
    assert updated["consolidation_version"] == 1
    assert any(created["node_type"] == "synthesis" for created in nodes)
    assert sparks[0]["spark_type"] == "thin_domain"
