from __future__ import annotations

import pytest

from neo.core.sparks import SparkGenerator
from neo.store.sqlite import SQLiteStore


class MockSparkLLM:
    async def generate(self, node, context, agent_focus=""):
        return [
            {"spark_type": "open_question", "description": "Question A"},
            {"spark_type": "weak_edge", "description": "Weak link"},
            {"spark_type": "thin_domain", "description": "Thin area"},
            {"spark_type": "isolated_node", "description": "Too many"},
        ]


@pytest.mark.asyncio
async def test_priority_scoring_formula():
    generator = SparkGenerator(store=None)  # type: ignore[arg-type]

    contradiction = generator.score_priority("contradiction", in_core_domain=True, is_recent=True, edge_count=0)
    thin_domain = generator.score_priority("thin_domain", in_core_domain=False, is_recent=False, edge_count=12)

    assert contradiction == 1.0
    assert thin_domain == 0.3


@pytest.mark.asyncio
async def test_domain_alignment_bonus_and_budget_enforcement(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo", domains=["memory"], config={"max_sparks_per_node": 2, "max_sparks_per_day": 2})
    node = await store.create_node(agent["id"], "concept", "Neo", "memory", summary="neo", confidence=0.8, parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="memory", metadata=None)
    generator = SparkGenerator(store, llm=MockSparkLLM())

    sparks = await generator.generate_for_node(agent=agent, node=node, max_sparks_per_node=2, max_sparks_per_day=2)

    assert len(sparks) == 2
    assert sparks[0]["priority"] >= 0.8


@pytest.mark.asyncio
async def test_spark_generation_with_mocked_llm(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo", domains=["memory"])
    node = await store.create_node(agent["id"], "concept", "Neo", "memory", summary="neo", confidence=0.8, parent_id=None, source_id=None, spark_id=None, embedding=[1.0], domain="memory", metadata=None)
    generator = SparkGenerator(store, llm=MockSparkLLM())

    sparks = await generator.generate_for_node(agent=agent, node=node)

    assert {spark["spark_type"] for spark in sparks} == {"open_question", "weak_edge", "thin_domain"}
