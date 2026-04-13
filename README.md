# Neo

Semantic knowledge for AI agents: a typed knowledge graph, semantic search,
research sparks, consolidation, MCP, REST, and a bundled visualizer.

Neo is not chat history. It stores what an agent has learned: concepts,
findings, theories, syntheses, and the relationships between them. Any agent
that can launch an MCP stdio server can use it.

See [`SPEC.md`](SPEC.md) for the product specification and
[`neo-architecture.md`](neo-architecture.md) for implementation architecture.

## Status

Neo is early alpha. The local install, MCP server, REST API, and visualizer work,
but public APIs may still change before a stable release. Do not store secrets
or private data unless you understand where your Neo database lives and how it
is backed up.

## Install

Neo requires Python 3.12 or newer. The easiest install path is `uv`, because
it can provide Python 3.12 even when `python3.12` is not on your PATH.

Install from GitHub:

```bash
uv tool install 'neo-agent-knowledge @ git+https://github.com/dallasrose/neo-agent-knowledge.git'
```

Install with stronger embeddings:

```bash
uv tool install 'neo-agent-knowledge[embeddings] @ git+https://github.com/dallasrose/neo-agent-knowledge.git'
```

Install with all optional integrations:

```bash
uv tool install 'neo-agent-knowledge[all] @ git+https://github.com/dallasrose/neo-agent-knowledge.git'
```

The installed command is `neo`.

If you already manage Python 3.12 yourself, pip works too:

```bash
python3.12 -m pip install 'neo-agent-knowledge @ git+https://github.com/dallasrose/neo-agent-knowledge.git'
```

## Start

Configure Neo once on this machine:

```bash
neo setup
neo status
```

For a local Ollama setup without prompts:

```bash
neo setup --provider ollama --model llama3.2 --non-interactive
```

`neo setup` configures Neo itself and initializes the database schema. It does
not create or configure an agent node; the agent does that for itself when it
connects.

Launch the local REST API and visualizer:

```bash
neo
```

This runs from Neo's user directory (`~/.neo`) and serves:

```text
http://127.0.0.1:8420
```

Start the MCP server over stdio for an agent host:

```bash
neo serve
```

`neo serve-rest` is still available as an explicit alias for the visualizer/API:

```bash
neo serve-rest
```

## Agent Setup

For Hermes or another local MCP host, add Neo as a stdio MCP server:

```json
{
  "mcpServers": {
    "neo": {
      "command": "neo",
      "args": ["serve"]
    }
  }
}
```

You can also generate this snippet:

```bash
neo mcp-config
```

Neo starts when the agent launches the MCP server. No separate daemon is
required for stdio mode.

Shared Neo settings such as the database, LLM, search keys, and discovery
configuration live in Neo's own config file, not in the agent platform.

Use `neo config-path` to print the exact file Neo reads.

Multiple agents can share the same Neo database/network. Each agent identity
gets its own root node inside that shared graph. If the launching platform needs
a distinct identity, pass a non-secret `--agent-name`:

```json
{
  "mcpServers": {
    "neo": {
      "command": "neo",
      "args": ["serve", "--agent-name", "hermes"]
    }
  }
}
```

If no agent name is provided, Neo uses `NEO_AGENT_NAME` from config or
`default`.

## Using Neo From An Agent

Neo exposes a small MCP tool surface for durable knowledge:

| Tool | Use |
| --- | --- |
| `get_neo_guidance` | Ask Neo how to use its tools. Agents should call this first when they are unsure. |
| `create_node` | Create a concept, finding, theory, or synthesis. If `parent_id` is omitted, Neo stores it under the agent root. |
| `update_node` | Edit content, summary, confidence, metadata, or `parent_id`. Use `parent_id` to reorganize knowledge. |
| `delete_node` | Delete a node. Child nodes become root-level nodes until updated. |
| `find_node_by_title` | Check for existing knowledge before creating a duplicate. |
| `get_node` / `get_branch` | Read a specific node or topic branch. |
| `link_nodes` | Add typed relationships such as `supports`, `contradicts`, or `extends`. |
| `search_knowledge` | Search durable knowledge before answering research-heavy questions. |
| `get_sparks` / `resolve_spark` / `abandon_spark` | Work Neo's research agenda. |
| `investigate_spark` | Run the standard research/debate/judge resolver for one spark. |
| `configure_agent` | Set the research direction, domains, skill notes, and optional source hints. |
| `trigger_discovery` | Run discovery immediately for the current research direction. |
| `ingest_youtube` | Ingest a YouTube transcript as distinct finding nodes. |

Sources are not knowledge parents. Use `configure_agent(...,
suggested_sources=[...])` to tell Neo which shows, channels, sites, or authors
should guide discovery. Neo stores source URL and title as metadata on ingested
nodes while the graph structure remains about the knowledge itself. Any source
can produce multiple nodes when it contains multiple durable learnings.

`store_node` is still available as a compatibility alias for `create_node`, but
new integrations should prefer `create_node`.

## Configuration

Neo works without API keys. By default it uses SQLite and deterministic fallback
embeddings so you can install it, initialize it, and connect an agent locally.
Configuration is loaded from environment variables first, then a local `.env`,
then `~/.neo/.env`. Put shared production settings in `~/.neo/.env`; use a
repo-local `.env` only for development.

Useful environment variables:

```bash
NEO_DATABASE_URL=sqlite+aiosqlite:////Users/you/.neo/neo.db
NEO_AGENT_NAME=hermes
NEO_EMBEDDING_API_KEY=...
NEO_LLM_PROVIDER=openai
NEO_LLM_MODEL=gpt-4.1-mini
NEO_LLM_BASE_URL=https://api.openai.com/v1
NEO_LLM_API_KEY=...
```

`NEO_DB_CONNECTION_URI` is also supported for existing installations.

Neo has one simple LLM configuration block. Task-specific values override it
when set: `NEO_LLM_SPARK_*`, `NEO_LLM_RESOLUTION_*`, and
`NEO_LLM_CONSOLIDATION_*`.

Supported LLM providers:

```bash
# OpenAI cloud
NEO_LLM_PROVIDER=openai
NEO_LLM_MODEL=gpt-4.1-mini
NEO_LLM_BASE_URL=https://api.openai.com/v1
NEO_LLM_API_KEY=...

# Local Ollama, LM Studio, vLLM, llama.cpp, or another OpenAI-compatible server
NEO_LLM_PROVIDER=ollama
NEO_LLM_MODEL=llama3.2
# NEO_LLM_BASE_URL defaults to http://127.0.0.1:11434/v1 for Ollama

# Anthropic-compatible cloud endpoints such as Claude or MiniMax
NEO_LLM_PROVIDER=anthropic
NEO_LLM_MODEL=claude-haiku-4-5
NEO_LLM_API_KEY=...
```

Spark generation and spark resolution ask models for strict JSON and tolerate
common wrapping such as Markdown fences or surrounding prose. Small local models
can work for simple spark generation, but autonomous spark resolution is a
judged debate/synthesis workflow and benefits from a stronger instruction
model.

Install tiers:

```bash
pip install neo-agent-knowledge              # SQLite + MCP + REST + visualizer
pip install neo-agent-knowledge[embeddings]  # OpenAI-compatible embeddings
pip install neo-agent-knowledge[sparks]      # Anthropic SDK + YouTube transcripts
pip install neo-agent-knowledge[postgres]    # PostgreSQL + pgvector
pip install neo-agent-knowledge[all]         # all optional integrations
```

## Local Development

From a checkout:

```bash
uv sync --extra dev
uv run neo init
uv run neo status
uv run neo serve
```

Run tests:

```bash
uv run pytest
```

## First REST Calls

Health:

```bash
curl http://127.0.0.1:8420/api/health
```

Store a node:

```bash
curl -X POST http://127.0.0.1:8420/api/nodes \
  -H 'content-type: application/json' \
  -d '{
    "node_type": "concept",
    "title": "Semantic Memory",
    "content": "Semantic memory stores structured understanding rather than session history.",
    "domain": "memory"
  }'
```

Search:

```bash
curl -X POST http://127.0.0.1:8420/api/search \
  -H 'content-type: application/json' \
  -d '{
    "query": "What do we know about semantic memory?",
    "domain": "memory"
  }'
```

Find a node by title:

```bash
curl "http://127.0.0.1:8420/api/nodes/by-title?title=Agents&domain=agents"
```

The response includes `selected_match` for the best candidate plus `matches`
and `ambiguous` so agents can detect duplicate titles and choose deliberately.

## Contributing

Contributions are welcome under the Apache License 2.0. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for setup and contribution terms.

## Security

Please do not open public issues for suspected vulnerabilities. See
[`SECURITY.md`](SECURITY.md).

## License

Neo is licensed under the Apache License 2.0. See [`LICENSE`](LICENSE) and
[`NOTICE`](NOTICE).
