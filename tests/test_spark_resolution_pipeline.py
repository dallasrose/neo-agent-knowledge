from __future__ import annotations

import pytest

from neo.core.api import NeoAPI
from neo.core.resolver import SparkResolver, _candidate_from_raw
from neo.store.sqlite import SQLiteStore


class StubEmbeddingClient:
    async def embed_text(self, title: str, content: str) -> list[float]:
        return [float(len(title) % 5), float(len(content) % 7)]


class NoopSparkGenerator:
    async def generate_for_node(self, **kwargs):
        return []


class NullSearch:
    async def multi_search(self, queries, **kwargs):
        return []


class DebateLLM:
    def __init__(self, winner: str = "AB", action: str = "create_node") -> None:
        self.winner = winner
        self.action = action

    async def call(self, prompt: str, max_tokens: int = 1024) -> str:
        if "ROLE: JUDGE_" in prompt:
            return f'{{"ranking":["{self.winner}","A","B"],"winner":"{self.winner}","rationale":"best resolves the spark"}}'
        if "ROLE: POSITION_AB" in prompt:
            return (
                '{"title":"Durable synthesis from the spark",'
                '"summary":"The spark resolves into a durable synthesis.",'
                '"content":"The graph context and evidence support a durable synthesis. '
                'This should be stored as knowledge rather than left as an open question.",'
                f'"recommended_action":"{self.action}",'
                '"node_type":"synthesis","confidence":0.82,"rationale":"synthesis wins"}'
            )
        if "ROLE: POSITION_A" in prompt:
            return (
                '{"title":"First plausible answer","summary":"One answer is plausible.",'
                '"content":"One evidence-backed answer is plausible from the graph context.",'
                '"recommended_action":"create_node","node_type":"finding","confidence":0.62}'
            )
        return (
            '{"title":"Second plausible answer","summary":"Another answer is plausible.",'
            '"content":"A second interpretation also fits some of the context.",'
            '"recommended_action":"create_node","node_type":"finding","confidence":0.6}'
        )


async def _setup(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient(), spark_generator=NoopSparkGenerator())
    target = await store.create_node(
        agent["id"],
        "concept",
        "Agent Research",
        "Agents can turn unresolved questions into durable knowledge.",
        summary="agents research questions",
        confidence=0.8,
        parent_id=None,
        source_id=None,
        spark_id=None,
        embedding=[1.0, 0.0],
        domain="agents",
        metadata=None,
    )
    spark = await store.create_spark(
        agent["id"],
        "open_question",
        "What should happen when a spark raises a new research idea?",
        priority=0.9,
        domain="agents",
        target_node_id=target["id"],
        source_id=None,
        metadata={},
    )
    spark = {
        **spark,
        "target_title": target["title"],
        "target_content": target["content"],
        "target_summary": target["summary"],
        "node_domain": target["domain"],
    }
    return store, api, agent, spark


@pytest.mark.asyncio
async def test_spark_resolution_pipeline_creates_node_and_resolves_spark(session_factory):
    store, api, agent, spark = await _setup(session_factory)
    resolver = SparkResolver(api, DebateLLM(winner="AB", action="create_node"), NullSearch())

    result = await resolver.resolve(spark, agent, mode="apply", trigger="background")

    assert result["success"] is True
    assert result["outcome"] == "created_node"
    resolved = (await store.get_sparks(agent["id"], status="resolved", limit=10))[0]
    assert resolved["id"] == spark["id"]
    assert resolved["resolved_node_id"] == result["node_id"]
    assert resolved["metadata"]["resolution_method"] == "debate_judge_v1"
    assert resolved["metadata"]["winner"] == "AB"
    assert await store.get_sparks(agent["id"], status="active", limit=10) == []


@pytest.mark.asyncio
async def test_spark_resolution_preview_does_not_mutate_graph(session_factory):
    store, api, agent, spark = await _setup(session_factory)
    resolver = SparkResolver(api, DebateLLM(winner="AB", action="create_node"), NullSearch())

    result = await resolver.resolve(spark, agent, mode="preview", trigger="manual")

    assert result["success"] is True
    assert result["outcome"] == "preview"
    assert result["winner"] == "AB"
    assert len(await store.get_sparks(agent["id"], status="active", limit=10)) == 1
    resolved = await store.get_sparks(agent["id"], status="resolved", limit=10)
    assert resolved == []


@pytest.mark.asyncio
async def test_spark_resolution_can_resolve_without_graph_change(session_factory):
    store, api, agent, spark = await _setup(session_factory)
    resolver = SparkResolver(api, DebateLLM(winner="AB", action="resolve_no_change"), NullSearch())

    result = await resolver.resolve(spark, agent, mode="apply", trigger="manual")

    assert result["success"] is True
    assert result["outcome"] == "resolved_no_change"
    resolved = (await store.get_sparks(agent["id"], status="resolved", limit=10))[0]
    assert resolved["metadata"]["resolved_node_ids"] == []
    assert resolved["metadata"]["winning_action"] == "resolve_no_change"


def test_candidate_parser_salvages_malformed_fenced_json():
    raw = (
        '```json { "title": "Tiered Constraint Architecture", '
        '"summary": "Fintech agents need deterministic controls.", '
        '"content": "Use policy gates, sandboxing, and approval boundaries.", '
        '"recommended_action": "create_node", "node_type": "synthesis", '
        '"confidence": 0.74'
    )

    candidate = _candidate_from_raw("AB", {}, raw)

    assert candidate["title"] == "Tiered Constraint Architecture"
    assert candidate["summary"] == "Fintech agents need deterministic controls."
    assert candidate["content"] == "Use policy gates, sandboxing, and approval boundaries."
    assert candidate["recommended_action"] == "create_node"
    assert candidate["node_type"] == "synthesis"
    assert candidate["confidence"] == 0.74
