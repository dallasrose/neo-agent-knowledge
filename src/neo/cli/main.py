import click

from neo.config import (
    get_config_dir,
    get_config_env_path,
    read_env_file,
    set_runtime_agent_name,
    settings,
    write_env_file,
)
from neo.core.consolidation import ConsolidationEngine
from neo.db import init_db
from neo.mcp.server import mcp
from neo.rest.app import app as rest_app
from neo.runtime import ensure_default_agent, get_api_singleton
from neo.store import create_store


def _serve_rest(host: str, port: int, agent_name: str | None) -> None:
    """Run the visualizer/API from Neo's user state directory."""
    import os
    import uvicorn

    set_runtime_agent_name(agent_name)
    os.chdir(get_config_dir())
    uvicorn.run(rest_app, host=host, port=port)


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Neo CLI.

    Running `neo` without a subcommand starts the local visualizer/API.
    """
    if ctx.invoked_subcommand is None:
        _serve_rest(settings.rest_host, settings.rest_port, None)


@cli.command()
@click.option(
    "--provider",
    type=click.Choice(["none", "ollama", "openai", "openrouter", "anthropic", "minimax"]),
    default=None,
    help="LLM provider to write into Neo's user config.",
)
@click.option("--model", default=None, help="LLM model name.")
@click.option("--base-url", default=None, help="Optional LLM API base URL.")
@click.option("--api-key", default=None, help="Optional LLM API key. Prefer --api-key-env for shared machines.")
@click.option("--api-key-env", default=None, help="Read the LLM API key from this environment variable.")
@click.option("--search-provider", default=None, help="Optional search provider, e.g. tavily or exa.")
@click.option("--search-api-key", default=None, help="Optional search API key.")
@click.option("--search-api-key-env", default=None, help="Read the search API key from this environment variable.")
@click.option("--enable-resolution/--disable-resolution", default=None, help="Enable or disable background spark resolution.")
@click.option("--resolution-interval-minutes", default=30, show_default=True, type=int)
@click.option("--resolution-batch-size", default=3, show_default=True, type=int)
@click.option("--non-interactive", is_flag=True, help="Do not prompt; use provided flags and defaults.")
def setup(
    provider: str | None,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    api_key_env: str | None,
    search_provider: str | None,
    search_api_key: str | None,
    search_api_key_env: str | None,
    enable_resolution: bool | None,
    resolution_interval_minutes: int,
    resolution_batch_size: int,
    non_interactive: bool,
) -> None:
    """Configure this Neo installation without creating an agent node."""
    import asyncio
    import os

    config_path = get_config_env_path()
    values = read_env_file(config_path)

    chosen_provider = provider
    if chosen_provider is None and not non_interactive:
        chosen_provider = click.prompt(
            "LLM provider",
            default=values.get("NEO_LLM_PROVIDER", "ollama"),
            type=click.Choice(["none", "ollama", "openai", "openrouter", "anthropic", "minimax"]),
        )
    chosen_provider = chosen_provider or values.get("NEO_LLM_PROVIDER") or "ollama"

    if chosen_provider == "none":
        for key in ("NEO_LLM_PROVIDER", "NEO_LLM_MODEL", "NEO_LLM_BASE_URL", "NEO_LLM_API_KEY"):
            values.pop(key, None)
        resolved_resolution = False if enable_resolution is None else enable_resolution
    else:
        default_model = {
            "ollama": "llama3.2",
            "openai": "gpt-4.1-mini",
            "openrouter": "anthropic/claude-sonnet-4",
            "anthropic": "claude-haiku-4-5",
            "minimax": "MiniMax-M2.7",
        }[chosen_provider]
        chosen_model = model
        if chosen_model is None and not non_interactive:
            chosen_model = click.prompt("LLM model", default=values.get("NEO_LLM_MODEL", default_model))
        chosen_model = chosen_model or values.get("NEO_LLM_MODEL") or default_model

        resolved_base_url = base_url
        if resolved_base_url is None:
            defaults = {
                "ollama": "http://127.0.0.1:11434/v1",
                "openai": "https://api.openai.com/v1",
                "openrouter": "https://openrouter.ai/api/v1",
                "minimax": "https://api.minimax.io/anthropic",
            }
            resolved_base_url = values.get("NEO_LLM_BASE_URL") or defaults.get(chosen_provider)
            if not non_interactive and resolved_base_url:
                resolved_base_url = click.prompt("LLM base URL", default=resolved_base_url)

        resolved_api_key = api_key
        if api_key_env:
            resolved_api_key = os.environ.get(api_key_env)
            if not resolved_api_key:
                raise click.ClickException(f"{api_key_env} is not set")
        if resolved_api_key is None and not non_interactive and chosen_provider not in {"ollama"}:
            existing = values.get("NEO_LLM_API_KEY")
            prompt_value = click.prompt(
                "LLM API key",
                default=existing or "",
                hide_input=True,
                show_default=bool(existing),
            )
            resolved_api_key = prompt_value or None

        values["NEO_LLM_PROVIDER"] = chosen_provider
        values["NEO_LLM_MODEL"] = chosen_model
        if resolved_base_url:
            values["NEO_LLM_BASE_URL"] = resolved_base_url
        if resolved_api_key:
            values["NEO_LLM_API_KEY"] = resolved_api_key
        resolved_resolution = enable_resolution
        if resolved_resolution is None:
            resolved_resolution = chosen_provider == "ollama" or bool(resolved_api_key)

    values["NEO_RESOLUTION_ENABLED"] = "true" if resolved_resolution else "false"
    values["NEO_RESOLUTION_INTERVAL_MINUTES"] = str(resolution_interval_minutes)
    values["NEO_RESOLUTION_BATCH_SIZE"] = str(resolution_batch_size)

    resolved_search_key = search_api_key
    if search_api_key_env:
        resolved_search_key = os.environ.get(search_api_key_env)
        if not resolved_search_key:
            raise click.ClickException(f"{search_api_key_env} is not set")
    if search_provider:
        values["NEO_SEARCH_PROVIDER"] = search_provider
    if resolved_search_key:
        values["NEO_SEARCH_API_KEY"] = resolved_search_key

    write_env_file(values, config_path)
    asyncio.run(init_db())

    click.echo(f"Neo config written: {config_path}")
    click.echo("Neo database initialized.")
    click.echo(
        "Background spark resolution: "
        f"{'enabled' if values['NEO_RESOLUTION_ENABLED'] == 'true' else 'disabled'} "
        f"(every {resolution_interval_minutes} min, batch {resolution_batch_size})"
    )
    click.echo("No agent node was created. Agent roots are created when an agent connects.")
    click.echo("MCP config example:")
    click.echo('{"mcpServers":{"neo":{"command":"neo","args":["serve","--agent-name","YOUR_AGENT_NAME"]}}}')


@cli.command("mcp-config")
@click.option("--name", default="neo", show_default=True, help="MCP server name.")
@click.option("--command", default="neo", show_default=True, help="Command used by the agent host.")
@click.option("--agent-name", default=None, help="Optional agent identity for a shared Neo network.")
def mcp_config(name: str, command: str, agent_name: str | None) -> None:
    """Print a ready-to-paste MCP stdio server config."""
    import json

    server: dict[str, object] = {
        "command": command,
        "args": ["serve"],
    }
    if agent_name:
        server["args"] = ["serve", "--agent-name", agent_name]

    click.echo(json.dumps({"mcpServers": {name: server}}, indent=2))


@cli.command()
@click.option("--agent-name", default=None, help="Optional agent identity to initialize.")
def init(agent_name: str | None) -> None:
    """Initialize the Neo database."""
    import asyncio

    set_runtime_agent_name(agent_name)

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
@click.option("--agent-name", default=None, help="Optional agent identity for a shared Neo network.")
def serve(transport: str, host: str | None, port: int | None, agent_name: str | None) -> None:
    """Start the MCP server.

    Use --transport http to expose Neo as a remote MCP endpoint compatible with
    Claude Managed Agents and other remote MCP clients. Protect the endpoint
    by setting NEO_MCP_API_KEY; requests must then include an
    X-Neo-Api-Key header matching that value.
    """
    set_runtime_agent_name(agent_name)

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
@click.option("--agent-name", default=None, help="Optional agent identity for the visualizer/API.")
def serve_rest(host: str, port: int, agent_name: str | None) -> None:
    """Start the REST server."""
    _serve_rest(host, port, agent_name)


@cli.command("config-path")
def config_path() -> None:
    """Print Neo's user-level config file path."""
    click.echo(str(get_config_env_path()))


@cli.command("relationships")
@click.option("--agent-name", default=None, help="Only process this agent. Defaults to every agent.")
@click.option("--limit", default=1000, show_default=True, help="Max nodes/edges to process per agent.")
@click.option("--build/--no-build", default=True, show_default=True, help="Create missing generated relationships.")
@click.option("--reclassify/--no-reclassify", default=True, show_default=True, help="Rejudge generated relationships.")
def relationships(agent_name: str | None, limit: int, build: bool, reclassify: bool) -> None:
    """Build and classify generated relationships for one or all agents."""
    import asyncio

    async def _run() -> None:
        await init_db()
        api = get_api_singleton()
        if agent_name:
            agent = await api.store.get_or_create_agent(agent_name)
            agents = [agent]
        else:
            agents = await api.store.list_agents()

        for agent in agents:
            build_result = {"nodes_processed": 0, "edges_created": 0}
            reclassify_result = {"edges_processed": 0, "edges_updated": 0, "edges_skipped": 0}
            if build:
                build_result = await api.build_relationships(agent_id=agent["id"], limit=limit)
            if reclassify:
                reclassify_result = await api.reclassify_relationships(agent_id=agent["id"], limit=limit)
            click.echo(
                f"{agent['name']}: "
                f"nodes={build_result['nodes_processed']} "
                f"created={build_result['edges_created']} "
                f"judged={reclassify_result['edges_processed']} "
                f"updated={reclassify_result['edges_updated']} "
                f"skipped={reclassify_result['edges_skipped']}"
            )

    asyncio.run(_run())


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
            "llm_provider": settings.llm_provider_for("resolution"),
            "llm_model": settings.llm_model_for("resolution"),
            "llm_configured": settings.llm_configured_for("resolution"),
            "resolution_enabled": settings.resolution_enabled,
            "resolution_interval_minutes": settings.resolution_interval_minutes,
            "resolution_batch_size": settings.resolution_batch_size,
            "consolidation_enabled": settings.consolidation_enabled,
            "rest_host": settings.rest_host,
            "rest_port": settings.rest_port,
        }
    )
