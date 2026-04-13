"""Integration tests for Neo's HTTP MCP transport.

Validates the full MCP session lifecycle over HTTP with auth middleware,
matching what Claude Managed Agents expects when connecting to Neo as a
remote MCP server.

Run with:
    pytest tests/test_mcp_http.py -v
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx
import pytest
import uvicorn
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from neo.core.api import NeoAPI
from neo.core.sparks import NullSparkLLM, SparkGenerator
from neo.db import Base
from neo.mcp import server as mcp_server
from neo.mcp.server import mcp

TEST_API_KEY = "test-managed-agents-key"
TEST_PORT = 18421


class _StubEmbeddingClient:
    """Returns a deterministic fixed-length vector for any input."""

    async def embed_text(self, title: str, content: str) -> list[float]:
        text = f"{title} {content}".lower()
        vec = [float((ord(c) % 17) / 17) for c in text[:16]]
        return vec + [0.0] * (16 - len(vec))


@pytest.fixture(scope="module")
def http_server(tmp_path_factory):
    """Spin up Neo's HTTP MCP server in a background thread for the module."""
    from fastmcp.server.http import create_streamable_http_app

    tmp = tmp_path_factory.mktemp("neo_http")
    db_url = f"sqlite+aiosqlite:///{tmp}/test.db"

    # Build an in-process API wired to a real (temp) DB
    engine = None
    session_factory = None

    async def _setup():
        nonlocal engine, session_factory
        engine = create_async_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    asyncio.run(_setup())

    api = NeoAPI(
        __import__("neo.store.sqlite", fromlist=["SQLiteStore"]).SQLiteStore(session_factory),
        embedding_client=_StubEmbeddingClient(),
        spark_generator=SparkGenerator(
            __import__("neo.store.sqlite", fromlist=["SQLiteStore"]).SQLiteStore(session_factory),
            llm=NullSparkLLM(),
        ),
    )

    async def _fake_get_api():
        return api

    # Patch the singleton so the server uses our in-process API
    original = mcp_server.get_api
    mcp_server.get_api = _fake_get_api

    class _ApiKeyMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            provided = (
                request.headers.get("X-Neo-Api-Key")
                or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            )
            if provided != TEST_API_KEY:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return await call_next(request)

    asgi_app = create_streamable_http_app(
        mcp,
        streamable_http_path="/mcp/",
        middleware=[Middleware(_ApiKeyMiddleware)],
        debug=False,
    )

    config = uvicorn.Config(asgi_app, host="127.0.0.1", port=TEST_PORT, log_level="error")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for the server to be ready
    for _ in range(30):
        try:
            httpx.get(f"http://127.0.0.1:{TEST_PORT}/mcp/", timeout=1)
        except Exception:
            pass
        time.sleep(0.1)

    yield f"http://127.0.0.1:{TEST_PORT}"

    server.should_exit = True
    thread.join(timeout=5)
    mcp_server.get_api = original
    if engine:
        asyncio.run(engine.dispose())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "X-Neo-Api-Key": TEST_API_KEY,
}


def _post(base_url: str, session_id: str | None, payload: dict) -> dict:
    """Send one JSON-RPC request and parse the SSE data: line back."""
    headers = dict(BASE_HEADERS)
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    with httpx.Client(timeout=10) as client:
        resp = client.post(f"{base_url}/mcp/", headers=headers, json=payload)
    # SSE responses start with "data: "
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    # Plain JSON response (e.g. auth error)
    try:
        return resp.json()
    except Exception:
        return {"_raw": resp.text, "_status": resp.status_code}


def _initialize(base_url: str) -> tuple[str, dict]:
    """Run the MCP initialize handshake and return (session_id, result)."""
    with httpx.Client(timeout=10) as client:
        resp = client.post(
            f"{base_url}/mcp/",
            headers=BASE_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "neo-test", "version": "0.1"},
                },
            },
        )
    session_id = resp.headers.get("mcp-session-id", "")
    assert session_id, "Server must return Mcp-Session-Id on initialize"

    result = None
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            result = json.loads(line[6:])
            break
    assert result and "result" in result

    # Send initialized notification (fire-and-forget, no response expected)
    _post(
        base_url,
        session_id,
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
    )
    return session_id, result["result"]


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


def test_no_api_key_returns_401(http_server):
    with httpx.Client(timeout=5) as client:
        resp = client.post(
            f"{http_server}/mcp/",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
    assert resp.status_code == 401


def test_wrong_api_key_returns_401(http_server):
    with httpx.Client(timeout=5) as client:
        resp = client.post(
            f"{http_server}/mcp/",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "X-Neo-Api-Key": "wrong-key",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
    assert resp.status_code == 401


def test_bearer_token_auth_accepted(http_server):
    """Managed Agents may send auth as 'Authorization: Bearer <key>'."""
    with httpx.Client(timeout=5) as client:
        resp = client.post(
            f"{http_server}/mcp/",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {TEST_API_KEY}",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0.1"},
                },
            },
        )
    # 406 means auth passed but MCP protocol handling took over
    assert resp.status_code in (200, 406), f"Unexpected: {resp.status_code} {resp.text[:200]}"


# ---------------------------------------------------------------------------
# MCP protocol tests
# ---------------------------------------------------------------------------


def test_initialize_handshake(http_server):
    session_id, result = _initialize(http_server)
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "neo"
    assert "tools" in result["capabilities"]


def test_tools_list_returns_all_tools(http_server):
    session_id, _ = _initialize(http_server)
    resp = _post(http_server, session_id, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = {t["name"] for t in resp["result"]["tools"]}
    expected = {
        "create_node", "store_node", "get_node", "get_branch", "find_node_by_title",
        "link_nodes", "update_node", "search_knowledge",
        "get_sparks", "investigate_spark", "resolve_spark", "abandon_spark", "get_activity_summary", "delete_node",
        "get_neo_guidance",
        "configure_agent", "get_agent_info", "trigger_discovery",
        "ingest_youtube",
    }
    assert expected == tools, f"Missing: {expected - tools}, Extra: {tools - expected}"


def test_neo_guidance_orients_model(http_server):
    session_id, _ = _initialize(http_server)
    resp = _post(
        http_server,
        session_id,
        {
            "jsonrpc": "2.0",
            "id": 20,
            "method": "tools/call",
            "params": {"name": "get_neo_guidance", "arguments": {}},
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert "semantic memory" in text
    assert "search_knowledge" in text
    assert "create_node" in text


def test_store_and_retrieve_node(http_server):
    session_id, _ = _initialize(http_server)

    # Store a node
    store_resp = _post(
        http_server,
        session_id,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "create_node",
                "arguments": {
                    "title": "HTTP Transport Test",
                    "content": "Neo can serve MCP over HTTP for remote agent frameworks.",
                    "node_type": "finding",
                    "confidence": 0.9,
                },
            },
        },
    )
    assert "result" in store_resp
    stored = json.loads(store_resp["result"]["content"][0]["text"])
    assert "id" in stored
    node_id = stored["id"]

    # Retrieve by title
    get_resp = _post(
        http_server,
        session_id,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "get_node",
                "arguments": {"node_id": "HTTP Transport Test"},
            },
        },
    )
    assert "result" in get_resp
    payload = json.loads(get_resp["result"]["content"][0]["text"])
    node = payload["node"]
    assert node["id"] == node_id
    assert node["title"] == "HTTP Transport Test"


def test_search_knowledge(http_server):
    session_id, _ = _initialize(http_server)

    # Store something first
    _post(
        http_server,
        session_id,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "store_node",
                "arguments": {
                    "title": "Managed Agents MCP Integration",
                    "content": "Claude Managed Agents connects to remote MCP servers over streamable HTTP.",
                    "node_type": "concept",
                },
            },
        },
    )

    # Search for it
    search_resp = _post(
        http_server,
        session_id,
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "search_knowledge",
                "arguments": {"query": "managed agents remote MCP"},
            },
        },
    )
    assert "result" in search_resp
    result = json.loads(search_resp["result"]["content"][0]["text"])
    # search_knowledge returns {nodes: [...], edges: [...], contradictions: [...], query: str}
    assert isinstance(result.get("nodes"), list)


def test_activity_summary(http_server):
    session_id, _ = _initialize(http_server)
    resp = _post(
        http_server,
        session_id,
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "get_activity_summary", "arguments": {}},
        },
    )
    assert "result" in resp
    summary = json.loads(resp["result"]["content"][0]["text"])
    assert "counts" in summary
    assert "period" in summary


def test_link_nodes(http_server):
    session_id, _ = _initialize(http_server)

    def store(idx, title, content):
        r = _post(
            http_server,
            session_id,
            {
                "jsonrpc": "2.0",
                "id": 10 + idx,
                "method": "tools/call",
                "params": {
                    "name": "store_node",
                    "arguments": {"title": title, "content": content, "node_type": "concept"},
                },
            },
        )
        return json.loads(r["result"]["content"][0]["text"])["id"]

    a_id = store(0, "MCP Protocol", "Model Context Protocol specification.")
    b_id = store(1, "HTTP Transport", "Streamable HTTP transport for MCP.")

    link_resp = _post(
        http_server,
        session_id,
        {
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {
                "name": "link_nodes",
                "arguments": {
                    "from_node_id": a_id,
                    "to_node_id": b_id,
                    "edge_type": "extends",
                    "description": "HTTP transport extends the base MCP spec",
                },
            },
        },
    )
    assert "result" in link_resp
    edge = json.loads(link_resp["result"]["content"][0]["text"])
    assert edge.get("from_node_id") == a_id
    assert edge.get("to_node_id") == b_id


def _call_tool(base_url, session_id, tool_name, arguments, req_id=20):
    return _post(base_url, session_id, {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    })


def test_resolve_spark_without_node_ids(http_server):
    """resolve_spark should work with just notes and no node_ids (false-positive resolution)."""
    session_id, _ = _initialize(http_server)

    # First create a node so we can generate a spark for it via the store
    store_resp = _call_tool(http_server, session_id, "store_node", {
        "title": "Resolve Test Node",
        "content": "A node to test spark resolution.",
        "node_type": "concept",
    }, req_id=20)
    node_id = json.loads(store_resp["result"]["content"][0]["text"])["id"]

    # Inject a spark directly via the API (bypasses LLM)
    import asyncio
    from neo.mcp.server import get_api
    from neo.runtime import ensure_default_agent

    async def _inject_spark():
        api = await get_api()
        agent = await ensure_default_agent(api)
        spark = await api.store.create_spark(
            agent["id"],
            "open_question",
            "Is this node complete?",
            priority=0.5,
            domain=None,
            target_node_id=node_id,
            source_id=None,
            metadata=None,
        )
        return spark["id"]

    spark_id = asyncio.run(_inject_spark())

    # Resolve with only notes, no node_ids
    resolve_resp = _call_tool(http_server, session_id, "resolve_spark", {
        "spark_id": spark_id,
        "notes": "Already covered by existing content — false positive.",
    }, req_id=21)
    assert "result" in resolve_resp, f"Error: {resolve_resp}"
    result = json.loads(resolve_resp["result"]["content"][0]["text"])
    assert result["status"] == "resolved"
    assert result["metadata"].get("notes") == "Already covered by existing content — false positive."


def test_abandon_spark(http_server):
    """abandon_spark should dismiss a false-positive spark with a reason."""
    session_id, _ = _initialize(http_server)

    store_resp = _call_tool(http_server, session_id, "store_node", {
        "title": "Abandon Test Node",
        "content": "A node to test spark abandonment.",
        "node_type": "concept",
    }, req_id=30)
    node_id = json.loads(store_resp["result"]["content"][0]["text"])["id"]

    import asyncio
    from neo.mcp.server import get_api
    from neo.runtime import ensure_default_agent

    async def _inject_spark():
        api = await get_api()
        agent = await ensure_default_agent(api)
        spark = await api.store.create_spark(
            agent["id"],
            "contradiction",
            "Misread tension that doesn't exist.",
            priority=0.3,
            domain=None,
            target_node_id=node_id,
            source_id=None,
            metadata=None,
        )
        return spark["id"]

    spark_id = asyncio.run(_inject_spark())

    abandon_resp = _call_tool(http_server, session_id, "abandon_spark", {
        "spark_id": spark_id,
        "reason": "The contradiction was a misread — both policies apply at different scopes.",
    }, req_id=31)
    assert "result" in abandon_resp, f"Error: {abandon_resp}"
    result = json.loads(abandon_resp["result"]["content"][0]["text"])
    assert result["status"] == "abandoned"


def test_resolve_spark_with_node_ids_still_works(http_server):
    """resolve_spark with node_ids should still validate and link them."""
    session_id, _ = _initialize(http_server)

    store_resp = _call_tool(http_server, session_id, "store_node", {
        "title": "Resolution Node",
        "content": "This node resolves the spark.",
        "node_type": "finding",
    }, req_id=40)
    node_id = json.loads(store_resp["result"]["content"][0]["text"])["id"]

    import asyncio
    from neo.mcp.server import get_api
    from neo.runtime import ensure_default_agent

    async def _inject_spark():
        api = await get_api()
        agent = await ensure_default_agent(api)
        spark = await api.store.create_spark(
            agent["id"],
            "open_question",
            "A question answered by a new node.",
            priority=0.7,
            domain=None,
            target_node_id=node_id,
            source_id=None,
            metadata=None,
        )
        return spark["id"]

    spark_id = asyncio.run(_inject_spark())

    resolve_resp = _call_tool(http_server, session_id, "resolve_spark", {
        "spark_id": spark_id,
        "node_ids": json.dumps([node_id]),
        "notes": "Answered directly.",
    }, req_id=41)
    assert "result" in resolve_resp, f"Error: {resolve_resp}"
    result = json.loads(resolve_resp["result"]["content"][0]["text"])
    assert result["status"] == "resolved"
