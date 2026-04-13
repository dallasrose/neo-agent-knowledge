import click

from neo.config import settings
from neo.core.consolidation import ConsolidationEngine
from neo.db import init_db
from neo.mcp.server import mcp
from neo.rest.app import app as rest_app
from neo.runtime import ensure_default_agent
from neo.store import create_store


@click.group()
def cli() -> None:
    """Neo CLI."""


@cli.command("mcp-config")
@click.option("--name", default="neo", show_default=True, help="MCP server name.")
@click.option("--command", default="neo", show_default=True, help="Command used by the agent host.")
@click.option("--agent-name", default=None, help="Optional NEO_AGENT_NAME value.")
def mcp_config(name: str, command: str, agent_name: str | None) -> None:
    """Print a ready-to-paste MCP stdio server config."""
    import json

    server: dict[str, object] = {
        "command": command,
        "args": ["serve"],
    }
    if agent_name:
        server["env"] = {"NEO_AGENT_NAME": agent_name}

    click.echo(json.dumps({"mcpServers": {name: server}}, indent=2))


@cli.command()
def init() -> None:
    """Initialize the Neo database."""
    import asyncio

    async def _run() -> None:
        await init_db()
        store = create_store()
        await store.get_or_create_agent(settings.agent_name)

    asyncio.run(_run())
    click.echo(f"Initialized Neo database and agent '{settings.agent_name}'")


@cli.command()
@click.option(
    "--transport",
    type=click.Choice(["stdio", "http"]),
    default="stdio",
    show_default=True,
    help="Transport protocol. Use 'http' for remote deployments (e.g. Claude Managed Agents).",
)
@click.option("--host", default=None, help="Bind host for HTTP transport (overrides NEO_MCP_HOST).")
@click.option("--port", default=None, type=int, help="Bind port for HTTP transport (overrides NEO_MCP_PORT).")
def serve(transport: str, host: str | None, port: int | None) -> None:
    """Start the MCP server.

    Use --transport http to expose Neo as a remote MCP endpoint compatible with
    Claude Managed Agents and other remote MCP clients. Protect the endpoint
    by setting NEO_MCP_API_KEY; requests must then include an
    X-Neo-Api-Key header matching that value.
    """
    if transport == "stdio":
        mcp.run()
        return

    # HTTP / streamable-http transport
    bind_host = host or settings.mcp_host
    bind_port = port or settings.mcp_port
    api_key = settings.mcp_api_key

    import uvicorn
    from fastmcp.server.http import create_streamable_http_app
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    middleware: list[Middleware] = []

    if api_key:
        class _ApiKeyMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                provided = (
                    request.headers.get("X-Neo-Api-Key")
                    or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                )
                if provided != api_key:
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)
                return await call_next(request)

        middleware.append(Middleware(_ApiKeyMiddleware))
        click.echo(f"Neo MCP (HTTP) listening on {bind_host}:{bind_port} — API key auth enabled")
    else:
        click.echo(f"Neo MCP (HTTP) listening on {bind_host}:{bind_port} — no auth (set NEO_MCP_API_KEY to protect)")

    asgi_app = create_streamable_http_app(mcp, streamable_http_path="/mcp/", middleware=middleware, debug=False)
    uvicorn.run(asgi_app, host=bind_host, port=bind_port)


@cli.command("serve-rest")
@click.option("--host", default=settings.rest_host, show_default=True)
@click.option("--port", default=settings.rest_port, type=int, show_default=True)
def serve_rest(host: str, port: int) -> None:
    """Start the REST server."""
    import uvicorn

    uvicorn.run(rest_app, host=host, port=port)


@cli.command()
@click.option("--batch", default=20, show_default=True, help="Max nodes to sample.")
def contemplate(batch: int) -> None:
    """Scan the graph for sparks that emerge naturally.

    Samples recently added nodes and isolated nodes as candidates.
    Not every node will produce a spark — that's correct behavior.
    The LLM returns [] when nothing interesting emerges.
    """
    import asyncio
    from datetime import datetime, timedelta, timezone

    async def _run() -> None:
        await init_db()
        api_obj = __import__("neo.runtime", fromlist=["get_api_singleton"]).get_api_singleton()
        agent = await ensure_default_agent(api_obj)
        agent_id = agent["id"]

        recent_cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        recent = await api_obj.store.get_nodes_by_agent(agent_id, since=recent_cutoff, limit=batch // 2)
        isolated = await api_obj.store.get_nodes_without_sparks(agent_id, limit=batch // 2)

        seen: set[str] = set()
        candidates = []
        for node in [*recent, *isolated]:
            if node["id"] not in seen:
                seen.add(node["id"])
                candidates.append(node)

        click.echo(f"Scanning {len(candidates)} candidate nodes...")
        sparked = 0
        for i, node in enumerate(candidates, 1):
            sparks = await api_obj.spark_generator.generate_for_node(agent=agent, node=node)
            if sparks:
                click.echo(f"  [{i}] {node['title']!r} → {len(sparks)} spark(s)")
                sparked += len(sparks)

        click.echo(f"Done. {sparked} spark(s) generated across {len(candidates)} candidates.")

    asyncio.run(_run())


@cli.command()
def consolidate() -> None:
    """Run one consolidation pass."""
    import asyncio

    async def _run() -> None:
        await init_db()
        store = create_store()
        agent = await ensure_default_agent()
        engine = ConsolidationEngine(store)
        result = await engine.run(agent["id"])
        click.echo(result)

    asyncio.run(_run())


@cli.command()
def status() -> None:
    """Show runtime configuration relevant for trying Neo locally."""
    click.echo(
        {
            "agent_name": settings.agent_name,
            "db_connection_uri": settings.db_connection_uri,
            "embedding_provider": settings.embedding_provider,
            "embedding_fallback_enabled": settings.embedding_fallback_enabled,
            "consolidation_enabled": settings.consolidation_enabled,
            "rest_host": settings.rest_host,
            "rest_port": settings.rest_port,
        }
    )
