import json

from neo.integrations.hermes.provider import NeoMemoryProvider


class FakeStore:
    async def get_or_create_agent(self, name):
        return {"id": "agent-1", "name": name}


class FakeAPI:
    def __init__(self):
        self.store = FakeStore()
        self.stored = []

    async def search_knowledge(self, **kwargs):
        return {
            "query": kwargs["query"],
            "nodes": [
                {
                    "id": "n1",
                    "title": "Lease Risk",
                    "node_type": "synthesis",
                    "summary": "lease due diligence",
                    "confidence": 0.9,
                    "similarity": 0.8,
                }
            ],
            "sparks": [],
        }

    async def get_node(self, **kwargs):
        return {"node": {"id": kwargs["node_id"], "title": "Node"}}

    async def get_sparks(self, **kwargs):
        return [{"description": "Open question", "priority": 0.6}]

    async def resolve_spark(self, **kwargs):
        return {"id": kwargs["spark_id"], "status": "resolved", "node_ids": kwargs.get("node_ids") or []}

    async def abandon_spark(self, **kwargs):
        return {"id": kwargs["spark_id"], "status": "abandoned", "reason": kwargs.get("reason")}

    async def store_node(self, **kwargs):
        self.stored.append(kwargs)
        return {"id": "stored-1", "title": kwargs["title"]}

    async def ingest_source_url(self, **kwargs):
        return {"nodes_created": 1, "url": kwargs["url"]}


def test_provider_prefetch_returns_signal(tmp_path):
    (tmp_path / "neo.json").write_text('{"agent_name":"atlas","signal_threshold":0.1}')
    provider = NeoMemoryProvider(api_factory=lambda: FakeAPI())
    provider.initialize("session-1", hermes_home=str(tmp_path), agent_context="primary")

    text = provider.prefetch("studio lease")

    assert "Neo Semantic Memory Signals" in text
    assert "Lease Risk" in text


def test_provider_tools_include_expected_neo_tools():
    provider = NeoMemoryProvider(api_factory=lambda: FakeAPI())
    names = {schema["name"] for schema in provider.get_tool_schemas()}
    assert {
        "neo_search",
        "neo_remember",
        "neo_get_node",
        "neo_sparks",
        "neo_investigate_spark",
        "neo_resolve_spark",
        "neo_abandon_spark",
        "neo_ingest_url",
    }.issubset(names)


def test_provider_tool_call_routes_search(tmp_path):
    provider = NeoMemoryProvider(api_factory=lambda: FakeAPI())
    provider.initialize("session-1", hermes_home=str(tmp_path), agent_context="primary")

    result = json.loads(provider.handle_tool_call("neo_search", {"query": "lease"}))

    assert result["ok"] is True
    assert "Neo Search Results" in result["markdown"]


def test_provider_tool_call_routes_remember(tmp_path):
    fake = FakeAPI()
    provider = NeoMemoryProvider(api_factory=lambda: fake)
    provider.initialize("session-1", hermes_home=str(tmp_path), agent_context="primary")

    result = json.loads(
        provider.handle_tool_call(
            "neo_remember",
            {"title": "Useful Finding", "content": "Durable semantic fact", "node_type": "finding"},
        )
    )

    assert result["ok"] is True
    assert fake.stored[0]["title"] == "Useful Finding"
    assert fake.stored[0]["deduplicate"] is True


def test_provider_tool_call_routes_ingest_url(tmp_path):
    provider = NeoMemoryProvider(api_factory=lambda: FakeAPI())
    provider.initialize("session-1", hermes_home=str(tmp_path), agent_context="primary")

    result = json.loads(
        provider.handle_tool_call(
            "neo_ingest_url",
            {"url": "https://example.com/research", "query_focus": "semantic memory"},
        )
    )

    assert result["ok"] is True
    assert result["result"]["nodes_created"] == 1


def test_provider_tool_call_routes_spark_resolution(tmp_path):
    provider = NeoMemoryProvider(api_factory=lambda: FakeAPI())
    provider.initialize("session-1", hermes_home=str(tmp_path), agent_context="primary")

    result = json.loads(
        provider.handle_tool_call(
            "neo_resolve_spark",
            {"spark_id": "spark-1", "node_ids": ["node-1"], "notes": "researched"},
        )
    )

    assert result["ok"] is True
    assert result["result"]["status"] == "resolved"
    assert result["result"]["node_ids"] == ["node-1"]


def test_provider_tool_call_routes_spark_investigation(tmp_path):
    provider = NeoMemoryProvider(api_factory=lambda: FakeAPI())
    provider.initialize("session-1", hermes_home=str(tmp_path), agent_context="primary")
    provider._investigate_spark = lambda **kwargs: {"spark_id": kwargs["spark_id"], "mode": kwargs["mode"]}

    result = json.loads(
        provider.handle_tool_call(
            "neo_investigate_spark",
            {"spark_id": "spark-1", "mode": "preview"},
        )
    )

    assert result["ok"] is True
    assert result["result"] == {"spark_id": "spark-1", "mode": "preview"}
