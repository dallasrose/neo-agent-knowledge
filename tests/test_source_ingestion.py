from __future__ import annotations

import pytest

from neo.core.api import NeoAPI
from neo.core.discovery import DiscoveryJob, extract_knowledge_findings
from neo.store.sqlite import SQLiteStore


class StubEmbeddingClient:
    async def embed_text(self, title: str, content: str) -> list[float]:
        return [float(len(title) % 7), float(len(content) % 11)]


class NoopSparkGenerator:
    async def generate_for_node(self, **kwargs):
        return []


@pytest.mark.asyncio
async def test_source_extraction_creates_distinct_knowledge_findings():
    findings = await extract_knowledge_findings(
        source_title="This AI made me $2,345 in 24 hours",
        source_text=(
            "Autonomous trading agents require strict risk budgets. "
            "Profit claims without audited logs should be treated as anecdotal evidence. "
            "Prompt-only brokerage workflows need guardrails before they can execute trades."
        ),
        source_type="youtube",
        source_url="https://youtu.be/example",
        max_findings=4,
        confidence=0.6,
    )

    assert len(findings) == 3
    assert all(finding["title"] != "This AI made me $2,345 in 24 hours" for finding in findings)
    assert findings[0]["title"] == "Autonomous trading agents require strict risk budgets"
    assert findings[1]["title"] == "Profit claims without audited logs should be treated as anecdotal evidence"


@pytest.mark.asyncio
async def test_youtube_storage_uses_source_title_as_metadata_not_node_title(session_factory, monkeypatch):
    from neo.core import youtube as youtube_module

    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient(), spark_generator=NoopSparkGenerator())
    job = DiscoveryJob(api)

    class FakeFetcher:
        def fetch(self, video_id: str):
            return {
                "text": (
                    "Autonomous trading agents require strict risk budgets. "
                    "Profit claims without audited logs should be treated as anecdotal evidence. "
                    "Prompt-only brokerage workflows need guardrails before they can execute trades."
                )
            }

    monkeypatch.setattr(youtube_module, "get_fetcher", lambda: FakeFetcher())

    source_title = "This AI made me $2,345 in 24 hours"
    results = await job._store_youtube_video(
        agent,
        video_id="abc12345678",
        title=source_title,
        url="https://www.youtube.com/watch?v=abc12345678",
        domain="agentic-ai",
    )

    nodes = await store.get_nodes_by_agent(agent["id"], limit=20)
    stored_findings = [node for node in nodes if (node.get("metadata") or {}).get("video_id") == "abc12345678"]

    assert len(results) == 3
    assert len(stored_findings) == 3
    assert all(node["title"] != source_title for node in stored_findings)
    assert {node["metadata"]["source_title"] for node in stored_findings} == {source_title}
    assert {node["metadata"]["findings_total"] for node in stored_findings} == {3}
