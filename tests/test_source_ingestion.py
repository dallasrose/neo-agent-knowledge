from __future__ import annotations

import re

import httpx
import pytest

from neo.core.api import NeoAPI
from neo.core.discovery import DiscoveryJob, IngestionProviderError, extract_knowledge_findings, prefilter_source_text, _clean_source_text
from neo.store.sqlite import SQLiteStore


class StubEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def embed_text(self, title: str, content: str) -> list[float]:
        self.calls.append((title, content))
        return [float(len(title) % 7), float(len(content) % 11)]


class NoopSparkGenerator:
    async def generate_for_node(self, **kwargs):
        return []


class TranscriptPrefilterLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def call(self, prompt: str, max_tokens: int = 1200) -> str:
        self.prompts.append(prompt)
        core = (
            "Agent memory needs provenance-aware retrieval before advice. "
            "Source metadata lets the agent separate durable research from casual notes. "
            "Retrieval quality improves when durable findings preserve source grounding and recall cues. "
        )
        return core * 20


class NoisyExtractionLLM:
    async def call(self, prompt: str, max_tokens: int = 1200) -> str:
        return """
[
  {
    "title": "By the way, I don't have a psychosis",
    "summary": "Podcast banter.",
    "content": "By the way, I don't have a psychosis. Why is everyone making that joke?",
    "confidence": 0.7
  },
  {
    "title": "I want to thank our new sponsor, Mail Trap",
    "summary": "Sponsor read.",
    "content": "I want to thank our new sponsor, Mail Trap. They integrate straight into your code with their SDKs.",
    "confidence": 0.7
  },
  {
    "title": "Agent QA environments need behavior monitoring before deployment",
    "summary": "Agent QA should monitor unauthorized behavior patterns before production rollout.",
    "content": "Agent QA environments need monitoring for unauthorized behavior patterns before deployment. This gives teams a signal for emergent failures before production.",
    "confidence": 0.82
  }
]
"""


class RecordingResearchLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def call(self, prompt: str, max_tokens: int = 1200) -> str:
        self.calls += 1
        return '["agent memory research interview"]'


class RecordingIngestionLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def call(self, prompt: str, max_tokens: int = 1200) -> str:
        self.calls += 1
        if self.calls > 1:
            return "[]"
        return """
[
  {
    "title": "Agent memory needs provenance-aware retrieval",
    "summary": "Semantic memory should preserve source provenance when recalling research.",
    "content": "Agent memory needs provenance-aware retrieval before it can reliably support advice. Source metadata lets the agent distinguish durable research from casual notes.",
    "confidence": 0.82
  }
]
"""


class ChunkAwareExtractionLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def call(self, prompt: str, max_tokens: int = 1200) -> str:
        self.prompts.append(prompt)
        marker = ""
        if "ALPHA_CHUNK_SIGNAL" in prompt:
            marker = "Alpha"
        elif "BETA_CHUNK_SIGNAL" in prompt:
            marker = "Beta"
        elif "GAMMA_CHUNK_SIGNAL" in prompt:
            marker = "Gamma"
        else:
            return "[]"
        return f'''
[
  {{
    "title": "{marker} operating principle should be preserved",
    "summary": "The {marker.lower()} section contains a durable operating principle.",
    "content": "The {marker.lower()} section explains that systems should preserve useful knowledge from the entire source rather than only the opening segment.",
    "confidence": 0.84
  }}
]
'''


class ContinuingExtractionLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def call(self, prompt: str, max_tokens: int = 1200) -> str:
        self.calls += 1
        if self.calls == 1:
            title = "First principle from dense chunk"
        elif self.calls == 2:
            title = "Second principle from dense chunk"
        else:
            return "[]"
        return f'''
[
  {{
    "title": "{title}",
    "summary": "Dense chunks may contain more than one useful principle.",
    "content": "A single transcript chunk can contain multiple durable ideas. Extraction should continue until no additional useful findings remain.",
    "confidence": 0.83
  }}
]
'''


class FailingExtractionLLM:
    async def call(self, prompt: str, max_tokens: int = 1200) -> str:
        raise RuntimeError("provider down")


class QuotaFailingExtractionLLM:
    async def call(self, prompt: str, max_tokens: int = 1200) -> str:
        request = httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/models/gemini:generateContent")
        response = httpx.Response(
            429,
            request=request,
            json={"error": {"status": "RESOURCE_EXHAUSTED", "message": "spending cap exceeded"}},
        )
        raise httpx.HTTPStatusError("429 RESOURCE_EXHAUSTED spending cap exceeded", request=request, response=response)


class FakeYouTubeSearch:
    async def search(self, query: str, max_results: int = 5, **kwargs):
        return [
            {
                "video_id": "abc12345678",
                "title": "Agent memory research interview",
                "url": "https://www.youtube.com/watch?v=abc12345678",
                "description": "Research conversation about agent memory.",
                "published_at": None,
                "channel_title": "Research Channel",
                "duration": "",
            }
        ]


def test_clean_source_text_removes_transcript_boilerplate():
    cleaned = _clean_source_text(
        "[00:01:02] HOST: Transcript music Agent systems require provenance tracking before deployment. https://example.com"
    )

    assert "00:01:02" not in cleaned
    assert "HOST:" not in cleaned
    assert "Transcript" not in cleaned
    assert "https://" not in cleaned
    assert "Agent systems require provenance tracking before deployment" in cleaned


@pytest.mark.asyncio
async def test_prefilter_source_text_removes_low_value_transcript_filler():
    llm = TranscriptPrefilterLLM()
    noisy_transcript = " ".join([
        "Welcome back to the show and remember to like and subscribe.",
        "Our sponsor today has a promo code and discount code.",
        "Agent memory needs provenance-aware retrieval before advice.",
        "Source metadata lets the agent separate durable research from casual notes.",
    ] * 180)

    filtered, metadata = await prefilter_source_text(
        source_title="Agent memory interview",
        source_text=noisy_transcript,
        source_type="youtube",
        source_url="https://youtu.be/example",
        agent_focus="agent memory provenance",
        llm=llm,
    )

    assert llm.prompts
    assert metadata["prefilter_applied"] is True
    assert metadata["prefilter_reduction_ratio"] > 0
    assert "provenance-aware retrieval" in filtered
    assert "promo code" not in filtered.lower()


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
async def test_long_source_extraction_processes_all_chunks_without_global_cap():
    llm = ChunkAwareExtractionLLM()
    source_text = " ".join(
        [
            "ALPHA_CHUNK_SIGNAL " + "alpha durable operating principle " * 900,
            "BETA_CHUNK_SIGNAL " + "beta durable operating principle " * 900,
            "GAMMA_CHUNK_SIGNAL " + "gamma durable operating principle " * 900,
        ]
    )

    findings = await extract_knowledge_findings(
        source_title="Long operator podcast",
        source_text=source_text,
        source_type="youtube",
        source_url="https://youtu.be/long-example",
        agent_focus="operating principle systems",
        llm=llm,
        max_findings=None,
        confidence=0.6,
    )

    titles = {finding["title"] for finding in findings}
    assert len(llm.prompts) >= 3
    assert "Alpha operating principle should be preserved" in titles
    assert "Beta operating principle should be preserved" in titles
    assert "Gamma operating principle should be preserved" in titles


class NeverEndingExtractionLLM:
    async def call(self, prompt: str, max_tokens: int = 1200) -> str:
        pass_match = re.search(r"Extraction pass: (\d+)", prompt)
        pass_index = int(pass_match.group(1)) if pass_match else 0
        return f'''
[
  {{
    "title": "Guard pressure finding {pass_index}",
    "summary": "This finding keeps the extractor producing new output.",
    "content": "A broken or unusually dense extraction can keep producing new findings. Neo should log when the continuation guard stops extraction.",
    "confidence": 0.81
  }}
]
'''


@pytest.mark.asyncio
async def test_single_chunk_extraction_continues_until_no_new_findings():
    llm = ContinuingExtractionLLM()

    findings = await extract_knowledge_findings(
        source_title="Dense operator podcast",
        source_text="Dense podcast segment about extracting every durable principle from long transcripts.",
        source_type="youtube",
        source_url="https://youtu.be/dense-example",
        agent_focus="operating principle systems",
        llm=llm,
        max_findings=None,
        confidence=0.6,
    )

    titles = [finding["title"] for finding in findings]
    assert llm.calls == 3
    assert titles == ["First principle from dense chunk", "Second principle from dense chunk"]


@pytest.mark.asyncio
async def test_extraction_guard_hit_is_logged_and_marked(caplog):
    findings = await extract_knowledge_findings(
        source_title="Dense runaway podcast",
        source_text="Dense source text with endless extractable claims.",
        source_type="youtube",
        source_url="https://youtu.be/runaway-example",
        agent_focus="audit extraction behavior",
        llm=NeverEndingExtractionLLM(),
        max_findings=None,
        confidence=0.6,
    )

    assert len(findings) == 25
    assert any(finding.get("extraction_guard_hit") is True for finding in findings)
    assert "extraction continuation guard hit" in caplog.text


@pytest.mark.asyncio
async def test_source_extraction_rejects_banter_and_sponsor_reads():
    findings = await extract_knowledge_findings(
        source_title="Founder podcast episode",
        source_text=(
            "By the way, I don't have a psychosis. Why is everyone making that joke? "
            "I want to thank our new sponsor, Mail Trap. They integrate straight into your code with their SDKs. "
            "Agent QA environments need monitoring for unauthorized behavior patterns before deployment."
        ),
        source_type="youtube",
        source_url="https://youtu.be/example",
        agent_focus="agentic AI coding agents",
        llm=NoisyExtractionLLM(),
        max_findings=4,
        confidence=0.6,
    )

    assert len(findings) == 1
    assert findings[0]["title"] == "Agent QA environments need behavior monitoring before deployment"
    assert "sponsor" not in findings[0]["content"].lower()
    assert "psychosis" not in findings[0]["content"].lower()


@pytest.mark.asyncio
async def test_fallback_extraction_returns_empty_for_only_low_value_transcript():
    findings = await extract_knowledge_findings(
        source_title="Founder podcast episode",
        source_text=(
            "By the way, I don't have a psychosis. Why is everyone making that joke? "
            "I want to thank our new sponsor, Mail Trap. They integrate straight into your code with their SDKs. "
            "You contact humans, not AI chat bots."
        ),
        source_type="youtube",
        source_url="https://youtu.be/example",
        agent_focus="agentic AI coding agents",
        max_findings=4,
        confidence=0.6,
    )

    assert findings == []


@pytest.mark.asyncio
async def test_llm_extraction_fallback_is_visible_on_findings():
    findings = await extract_knowledge_findings(
        source_title="Risk memo",
        source_text="Autonomous trading agents require strict risk budgets before deployment.",
        source_type="web",
        source_url="https://example.com/risk",
        agent_focus="autonomous agents risk budgets",
        llm=FailingExtractionLLM(),
        max_findings=2,
        confidence=0.6,
    )

    assert findings
    assert findings[0]["llm_fallback_used"] is True
    assert findings[0]["llm_fallback_reason"] == "ingestion_llm_error:RuntimeError"


@pytest.mark.asyncio
async def test_llm_quota_failure_raises_without_heuristic_fallback():
    with pytest.raises(IngestionProviderError) as excinfo:
        await extract_knowledge_findings(
            source_title="Risk memo",
            source_text="Autonomous trading agents require strict risk budgets before deployment.",
            source_type="web",
            source_url="https://example.com/risk",
            agent_focus="autonomous agents risk budgets",
            llm=QuotaFailingExtractionLLM(),
            max_findings=2,
            confidence=0.6,
            allow_heuristic_fallback=False,
        )

    assert "provider_unhealthy:HTTP 429" in excinfo.value.reason


@pytest.mark.asyncio
async def test_missing_ingestion_provider_does_not_write_nodes(session_factory, monkeypatch):
    from neo.config import settings

    monkeypatch.setattr(settings, "ingestion_allow_heuristic_fallback", False)
    monkeypatch.setattr(type(settings), "llm_configured_for", lambda self, task: False)
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo", specialty="agentic memory systems")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient(), spark_generator=NoopSparkGenerator())

    result = await api.ingest_source_text(
        agent_id=agent["id"],
        title="Research memo",
        text="Agent memory needs provenance-aware retrieval before advice.",
        source_type="web",
        reference="https://example.com/memo",
    )

    nodes = await store.get_nodes_by_agent(agent["id"], limit=20)
    assert result["status"] == "failed"
    assert result["nodes_created"] == 0
    assert result["failure_reason"] == "ingestion_llm_provider_missing_or_unconfigured"
    assert nodes == []


@pytest.mark.asyncio
async def test_ingestion_provider_429_does_not_write_or_count_fallback(session_factory, monkeypatch):
    import neo.core.llm as llm_module
    from neo.config import settings

    monkeypatch.setattr(settings, "ingestion_allow_heuristic_fallback", False)
    monkeypatch.setattr(settings, "llm_ingestion_provider", "gemini")
    monkeypatch.setattr(settings, "llm_ingestion_model", "gemini-3.5-flash")
    monkeypatch.setattr(settings, "llm_ingestion_api_key", "test-key")
    monkeypatch.setattr(llm_module, "NeoLLMClient", lambda **kwargs: QuotaFailingExtractionLLM())
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo", specialty="agentic memory systems")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient(), spark_generator=NoopSparkGenerator())

    result = await api.ingest_source_text(
        agent_id=agent["id"],
        title="Risk memo",
        text="Autonomous trading agents require strict risk budgets before deployment.",
        source_type="web",
        reference="https://example.com/risk",
    )

    nodes = await store.get_nodes_by_agent(agent["id"], limit=20)
    assert result["status"] == "failed"
    assert result["nodes_created"] == 0
    assert result["provider_status"] == "unhealthy"
    assert "HTTP 429" in result["failure_reason"]
    assert nodes == []


@pytest.mark.asyncio
async def test_youtube_storage_uses_source_title_as_metadata_not_node_title(session_factory, monkeypatch):
    from neo.core import youtube as youtube_module

    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient(), spark_generator=NoopSparkGenerator())
    job = DiscoveryJob(api, ingestion_llm=RecordingIngestionLLM())

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

    assert len(results) == len(stored_findings)
    assert len(stored_findings) > 0
    assert all(node["title"] != source_title for node in stored_findings)
    assert {node["metadata"]["source_title"] for node in stored_findings} == {source_title}
    assert {node["metadata"]["findings_total"] for node in stored_findings} == {len(stored_findings)}


@pytest.mark.asyncio
async def test_discovery_uses_research_llm_for_queries_and_ingestion_llm_for_extraction(session_factory, monkeypatch):
    from neo.core import youtube as youtube_module

    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent(
        "neo",
        specialty="agentic memory systems",
        domains=["agent-memory"],
    )
    api = NeoAPI(store, embedding_client=StubEmbeddingClient(), spark_generator=NoopSparkGenerator())
    research_llm = RecordingResearchLLM()
    ingestion_llm = RecordingIngestionLLM()
    job = DiscoveryJob(
        api,
        research_llm=research_llm,
        ingestion_llm=ingestion_llm,
        yt_search=FakeYouTubeSearch(),
    )

    class FakeFetcher:
        def fetch(self, video_id: str):
            return {
                "text": "Semantic memory should preserve source provenance when recalling research."
            }

    monkeypatch.setattr(youtube_module, "get_fetcher", lambda: FakeFetcher())

    result = await job.run(agent, batch_size=1, lookback_days=30)

    nodes = await store.get_nodes_by_agent(agent["id"], limit=20)
    stored = [
        node
        for node in nodes
        if (node.get("metadata") or {}).get("video_id") == "abc12345678"
    ]
    assert result["ingested"] == 1
    assert research_llm.calls == 1
    assert ingestion_llm.calls >= 2
    assert stored


@pytest.mark.asyncio
async def test_ingest_source_url_fetches_youtube_transcript(session_factory, monkeypatch):
    from neo.core import youtube as youtube_module

    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo", specialty="brand strategy")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient(), spark_generator=NoopSparkGenerator())

    class FakeFetcher:
        def fetch_url(self, url: str):
            return {
                "text": (
                    "Premium brands must create a clear status ladder for customers. "
                    "Brands become premium when the buying experience, narrative, and visual codes all signal scarce value."
                ),
                "duration_seconds": 180,
            }

    monkeypatch.setattr(youtube_module, "get_fetcher", lambda: FakeFetcher())

    result = await api.ingest_source_url(
        agent_id=agent["id"],
        url="https://www.youtube.com/watch?v=hDsqIH6Xai8",
        title="The Science of Building a Premium Brand",
        domain="brand-strategy",
        max_findings=3,
        allow_heuristic_fallback=True,
    )

    source = await store.get_source(result["source_id"])
    nodes = await store.get_nodes_by_agent(agent["id"], limit=20)
    stored = [node for node in nodes if (node.get("metadata") or {}).get("reference") == "https://www.youtube.com/watch?v=hDsqIH6Xai8"]

    assert result["nodes_created"] >= 1
    assert source is not None
    assert source["source_type"] == "youtube"
    assert "Premium brands must create" in source["content"]
    assert stored
    assert stored[0]["metadata"]["source_type"] == "youtube"


@pytest.mark.asyncio
async def test_ingest_source_text_stores_source_and_recall_cues(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo", specialty="agentic memory systems")
    embeddings = StubEmbeddingClient()
    api = NeoAPI(store, embedding_client=embeddings, spark_generator=NoopSparkGenerator())

    result = await api.ingest_source_text(
        agent_id=agent["id"],
        title="Research memo",
        text=(
            "Agent memory needs provenance-aware retrieval before it can reliably support advice. "
            "Source metadata lets the agent distinguish durable research from casual notes."
        ),
        source_type="web",
        reference="https://example.com/memo",
        domain="agent-memory",
        max_findings=2,
        allow_heuristic_fallback=True,
    )

    nodes = await store.get_nodes_by_agent(agent["id"], limit=20)
    stored = [node for node in nodes if (node.get("metadata") or {}).get("reference") == "https://example.com/memo"]

    assert result["nodes_created"] >= 1
    assert result["source_id"]
    source = await store.get_source(result["source_id"])
    assert source is not None
    assert source["content"] == (
        "Agent memory needs provenance-aware retrieval before it can reliably support advice. "
        "Source metadata lets the agent distinguish durable research from casual notes."
    )
    assert stored
    assert stored[0]["source_id"] == result["source_id"]
    assert stored[0]["metadata"]["recall_cues"]
    assert "Recall cues:" in embeddings.calls[-1][1]


@pytest.mark.asyncio
async def test_ingest_source_text_preserves_manual_source_confidence(session_factory):
    store = SQLiteStore(session_factory)
    agent = await store.get_or_create_agent("neo", specialty="agentic memory systems")
    api = NeoAPI(store, embedding_client=StubEmbeddingClient(), spark_generator=NoopSparkGenerator())

    result = await api.ingest_source_text(
        agent_id=agent["id"],
        title="Hand-picked research memo",
        text="Agent memory systems require provenance tracking to separate durable research from casual notes.",
        source_type="web",
        reference="https://example.com/endorsed",
        domain="agent-memory",
        source_confidence=0.91,
        user_endorsed=True,
        max_findings=1,
        allow_heuristic_fallback=True,
    )

    source = await store.get_source(result["source_id"])
    nodes = await store.get_nodes_by_agent(agent["id"], limit=20)
    stored = [node for node in nodes if (node.get("metadata") or {}).get("reference") == "https://example.com/endorsed"]

    assert source is not None
    assert source["metadata"]["source_confidence"] == 0.91
    assert source["metadata"]["user_endorsed"] is True
    assert stored[0]["confidence"] == 0.91
    assert stored[0]["metadata"]["source_confidence"] == 0.91
    assert stored[0]["metadata"]["user_endorsed"] is True
