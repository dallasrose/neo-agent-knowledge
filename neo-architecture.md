# Neo: Architecture & Implementation Plan

## Context

Neo is a semantic memory system for AI agents — the "knowledge layer" that stores structured understanding (concepts, findings, theories, questions) as a typed knowledge graph with vector search, background consolidation, and an autonomous research drive.

**Goal**: Build a working v1 that an agent can connect to via MCP and immediately use to store, search, link, and evolve knowledge.

---

## Project Structure

```
Neo/
  pyproject.toml
  .env.example
  .gitignore
  alembic.ini

  src/
    neo/
      __init__.py                # Package version
      enums.py                   # NodeType, EdgeType, SparkType, SparkStatus, SourceType
      config.py                  # Pydantic settings (env vars, ~/.neo/.env, local .env)
      db.py                      # Engine, session factory, Base, init_db()
      models.py                  # SQLAlchemy ORM: NeoAgent, NeoNode, NeoEdge, optional NeoSource, NeoSpark

      store/
        __init__.py              # create_store() factory
        interface.py             # Abstract StoreInterface (ABC)
        sqlite.py                # SQLite + sqlite-vec implementation
        postgres.py              # PostgreSQL + pgvector implementation

      embedding/
        __init__.py
        client.py                # Multi-provider embedding client (OpenAI default)

      core/
        __init__.py
        api.py                   # NeoAPI — the Python API (9 methods + internals)
        assembler.py             # Working Memory Assembly pipeline
        llm.py                   # Anthropic/OpenAI-compatible LLM normalization
        sparks.py                # Research Drive: spark generation + priority scoring
        consolidation.py         # Memory Consolidation engine (two-pass)
        scheduler.py             # Background consolidation scheduler

      mcp/
        __init__.py
        server.py                # FastMCP server — thin wrappers over NeoAPI

      rest/
        __init__.py
        app.py                   # FastAPI app
        routes.py                # REST route definitions
        schemas.py               # REST request/response schemas

      cli/
        __init__.py
        main.py                  # CLI: setup, init, serve, serve-rest, consolidate

  migrations/
    env.py                       # Alembic env (Postgres only)
    script.py.mako
    versions/

  tests/
    __init__.py
    conftest.py                  # Fixtures: in-memory SQLite, mock embedding client
    test_models.py
    test_store_interface.py
    test_api.py
    test_assembler.py
    test_sparks.py
    test_consolidation.py
    test_mcp_server.py
```

---

## Dependencies (`pyproject.toml`)

```toml
[project]
name = "neo-agent-knowledge"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "sqlalchemy[asyncio]>=2.0.30",
    "aiosqlite>=0.20.0",
    "sqlite-vec>=0.1",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "python-dotenv>=1.0",
    "tiktoken>=0.7",
    "croniter>=6.0",
    "fastmcp>=2.0",
    "fastapi[standard]>=0.115",
    "httpx>=0.27",
    "click>=8.1",
]

[project.optional-dependencies]
embeddings = ["openai>=1.30"]
sparks = ["anthropic>=0.49", "youtube-transcript-api>=0.6"]
postgres = ["psycopg[binary]>=3.1", "pgvector>=0.2.5", "alembic>=1.13"]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "pytest-cov>=5.0"]

[project.scripts]
neo = "neo.cli.main:cli"
```

Use `uv` for dependency management.

---

## Data Model (5 entities)

### Enums (`src/neo/enums.py`)

All as `StrEnum`:

- **NodeType**: `finding | concept | theory | question | idea | answer | synthesis`
- **EdgeType**: `supports | contradicts | prerequisite_for | extends | example_of | questions | resolves | inspired | connects`
- **SparkType**: `open_question | contradiction | weak_edge | isolated_node | thin_domain`
- **SparkStatus**: `active | resolved | abandoned`
- **SourceType**: `url | document | conversation | research_session | manual`

### SQLAlchemy Models (`src/neo/models.py`)

All use UUID string PKs (`str(uuid4())`), timezone-aware timestamps, JSON for flexible fields.

**NeoAgent** — identity anchor
- `id`, `name` (unique), `specialty` (text), `domains` (JSON list), `skill_notes` (text, NeoLang), `config` (JSON — max_sparks_per_node, max_sparks_per_day, model overrides, root_node_id, suggested_sources), `created_at`, `updated_at`

**NeoNode** — unit of knowledge
- `id`, `agent_id` (FK), `node_type`, `title`, `content` (full text), `summary` (NeoLang), `confidence` (0.0-1.0, check constraint), `parent_id` (FK self-ref, nullable), `source_id` (FK, nullable), `spark_id` (FK, nullable), `embedding` (Vector(1536) for PG / Text for SQLite), `domain`, `metadata` (JSON), `created_at`, `updated_at`, `consolidation_version` (int, incremented by consolidation)
- Indexes: `agent_id`, `parent_id`, `(agent_id, domain)`, `(agent_id, node_type)`, `created_at`, HNSW on `embedding` (PG only)

**NeoEdge** — typed relationship
- `id`, `agent_id` (FK), `from_node_id` (FK), `to_node_id` (FK), `edge_type`, `weight` (0.0-1.0, check constraint), `description` (NeoLang), `source_id` (FK, nullable), `metadata` (JSON), `created_at`, `updated_at`
- Indexes: `from_node_id`, `to_node_id`, `(from_node_id, to_node_id)`

**NeoSource** — optional shared provenance record
- `id`, `agent_id` (FK), `source_type`, `title`, `reference` (URL/path/ID), `content` (nullable, full text), `metadata` (JSON), `retrieved_at`

Common provenance should stay on node metadata as `url`, `source_title`, and
`source_type`. Sources are not graph parents; suggested sources live on
`NeoAgent.config.suggested_sources` and guide discovery.
Source ingestion extracts durable findings from source text first, so one
article, feed item, or video can create multiple knowledge nodes.

**NeoSpark** — research drive
- `id`, `agent_id` (FK), `spark_type`, `target_node_id` (FK, nullable — the node this spark is about), `description` (NeoLang), `priority` (0.0-1.0, check constraint), `status` (default "active"), `source_id` (FK, nullable), `resolved_node_id` (FK, nullable — the node this became), `domain`, `metadata` (JSON), `created_at`, `resolved_at` (nullable)
- Indexes: `(agent_id, status)`, `(agent_id, priority)`, `created_at`

**Embedding column handling**: Conditionally use `pgvector.sqlalchemy.Vector(1536)` for Postgres or `Text` for SQLite (JSON-serialized, searched via `sqlite-vec` virtual table).

---

## StoreInterface (`src/neo/store/interface.py`)

Abstract base class. All methods async. All return plain dicts (not ORM objects).

```python
class StoreInterface(ABC):
    # Agent
    async def get_or_create_agent(name, **kwargs) -> dict
    async def get_agent(agent_id) -> dict | None
    async def update_agent(agent_id, **kwargs) -> dict

    # Node CRUD
    async def create_node(agent_id, node_type, title, content, *, summary, confidence, parent_id, source_id, spark_id, embedding, domain, metadata) -> dict
    async def get_node(node_id) -> dict | None
    async def update_node(node_id, *, content, summary, confidence, embedding, metadata, consolidation_version) -> dict
    async def get_nodes_by_agent(agent_id, *, node_type, domain, since, limit, offset) -> list[dict]

    # Edge CRUD
    async def create_edge(agent_id, from_node_id, to_node_id, edge_type, *, weight, description, source_id, metadata) -> dict
    async def get_edges(node_id, *, direction="both", edge_type=None) -> list[dict]

    # Source CRUD
    async def create_source(agent_id, source_type, title, reference, *, content, metadata) -> dict
    async def get_source(source_id) -> dict | None

    # Spark CRUD
    async def create_spark(agent_id, spark_type, description, *, priority, domain, target_node_id, source_id, metadata) -> dict
    async def get_sparks(agent_id, *, status="active", spark_type, domain, min_priority, limit) -> list[dict]
    async def resolve_spark(spark_id, resolved_node_ids, *, notes, metadata) -> dict
    async def abandon_spark(spark_id, *, reason, metadata) -> dict

    # Vector Search
    async def vector_search(agent_id, query_embedding, *, top_k, node_type, domain, min_confidence) -> list[dict]

    # Graph Traversal
    async def get_neighborhood(node_id, *, depth, min_weight, edge_types) -> dict  # {nodes, edges}
    async def get_ancestors(node_id, max_depth) -> list[dict]
    async def get_descendants(node_id, max_depth) -> list[dict]

    # Activity
    async def get_activity(agent_id, since) -> dict

    # Consolidation
    async def get_unconsolidated_nodes(agent_id, *, since_version, limit) -> list[dict]
    async def count_nodes_since(agent_id, since) -> int
```

**Factory** (`store/__init__.py`): Reads `NEO_DB_CONNECTION_URI` — if starts with `sqlite`, returns `SQLiteStore`; otherwise `PostgresStore`.

### SQLite Backend (`store/sqlite.py`)
- Uses `aiosqlite` via SQLAlchemy async
- Vector search via `sqlite-vec` virtual table: `CREATE VIRTUAL TABLE neo_node_vectors USING vec0(node_id TEXT PRIMARY KEY, embedding float[1536])`
- On node create/update: write embedding to both main table (as JSON text) and virtual table

### PostgreSQL Backend (`store/postgres.py`)
- Uses `psycopg` via SQLAlchemy async
- Vector search via pgvector `<=>` cosine distance operator
- HNSW index on embedding column: `postgresql_using="hnsw"`, `postgresql_ops={"embedding": "vector_cosine_ops"}`

---

## Agent-Facing Tools — Implementation Logic

### 1. `create_node` → `NeoAPI.store_node()`
1. Validate `node_type` against enum
2. Default missing `parent_id` to the agent root node
3. Validate explicit `parent_id` belongs to the same agent
4. Generate embedding: `embed(f"{title}\n{content}")`
5. Generate NeoLang summary via LLM if not provided (or accept agent-provided summary)
6. `store.create_node(...)` — persist with embedding
7. **Background**: `asyncio.create_task(self._generate_sparks_for_node(agent_id, node))` — fire-and-forget spark generation via local model
8. Return `{id, title, node_type, confidence, sparks_pending: true}`

`store_node` remains as a compatibility alias for MCP clients that already use it.

### 2. `link_nodes` → `NeoAPI.link_nodes()`
1. Validate both node IDs exist
2. Validate `edge_type` against enum
3. `store.create_edge(...)`
4. If `edge_type == "contradicts"`: auto-create spark `{type: "contradiction", priority: 0.9, description: "Contradiction: {node_a.title} vs {node_b.title}"}`
5. Return edge dict

### 3. `update_node` → `NeoAPI.update_node()`
1. Get existing node
2. If content changed: re-embed
3. If `parent_id` changed: validate parent exists, same agent, not self, not descendant
4. Merge metadata updates for provenance such as source URL and source title
5. `store.update_node(...)` — preserves history in metadata
6. Signal consolidation pending
7. Return updated node dict

### 4. `get_node` → `NeoAPI.get_node()`
1. Validate node ID exists
2. Fetch node via `store.get_node(node_id)`
3. Optionally fetch `store.get_edges(node_id)`
4. Optionally fetch `store.get_ancestors(node_id)`
5. Optionally fetch immediate children
6. Return `{node, edges, ancestors, children}`

### 5. `get_branch` → `NeoAPI.get_branch()`
1. Validate root node ID exists
2. Fetch root node
3. Fetch descendants via `store.get_descendants(root_node_id, max_depth)`
4. Collect internal edges where both endpoints live inside the branch
5. Return `{root, nodes, edges, count, max_depth}`

### 6. `find_node_by_title` → `NeoAPI.find_node_by_title()`
1. Normalize the requested title
2. Fetch candidate nodes for the agent, optionally filtered by domain
3. Match either exact-title equality or substring inclusion
4. Rank matches so exact matches and higher-confidence nodes sort first
5. Return `{query, exact, domain, count, matches_returned, ambiguous, selected_match, matches}`

### 7. `search_knowledge` → `NeoAPI.search_knowledge()` → `WorkingMemoryAssembler.assemble()`

Six-step pipeline:
1. **Embed query** → query vector
2. **Semantic search** → `store.vector_search(query_embedding, top_k)` → seed nodes
3. **Graph expansion** → for each seed, `store.get_neighborhood(depth=hop_depth, min_weight)` — prioritize `supports` and `prerequisite_for` edges, flag `contradicts`
4. **Hierarchy surfacing** → `store.get_ancestors()` for each result — include parent summaries only
5. **Ranking** → score = `(semantic_similarity * 0.6) + (confidence * 0.2) + (recency * 0.2)` — penalty for open contradictions
6. **Compression** → take ranked nodes in order until `token_budget` reached — use `summary` field (NeoLang), not `content` — append contradiction warnings and active sparks

Returns `{nodes: [...], edges: [...], contradictions: [...], sparks: [...], total_candidates: N}`

### 8. `get_sparks` → `NeoAPI.get_sparks()`
1. `store.get_sparks(agent_id, status="active", ...)` — sorted by priority desc
2. Return list of spark dicts with descriptions

### 9. `investigate_spark` / background resolution → `SparkResolver.resolve()`
1. Collect target node, internal graph context, generated search queries, web results, and transcript excerpts when available
2. Run role-isolated Candidate A and Candidate B agents using spark-type-specific framing
3. Run Candidate AB synthesis agent
4. Run three blind judge agents over anonymised candidates
5. Apply the winning action: create node, update target, resolve with no graph change, or abandon
6. Store resolution metadata on the spark: method, trigger, candidates, judge votes, evidence, and winner

The MCP tool and `ResolutionScheduler` call the same resolver. Trigger metadata
changes; the process does not.

Spark-type framing is explicit:

- `contradiction`: A and B defend different claims/readings; AB reconciles,
  chooses, or preserves uncertainty.
- `open_question`: A and B answer from distinct evidence-backed perspectives;
  AB synthesizes the best current answer and remaining uncertainty.
- `weak_edge`: A argues the relationship is useful; B argues it is weak,
  indirect, misleading, or mistyped; AB decides graph treatment.
- `isolated_node`: A argues the strongest placement; B argues an alternative or
  no integration; AB decides whether to store, link, update, or close.
- `thin_domain`: A proposes the highest-value missing knowledge; B proposes an
  alternative or argues the gap is low value; AB decides the durable action.

Candidate actions:

- `create_node`: store a new durable finding/theory/synthesis.
- `update_target`: update the target node with the resolved insight.
- `resolve_no_change`: close the spark because existing knowledge is sufficient.
- `abandon`: close the spark as a false positive or low-value question.

### 10. `resolve_spark` → `NeoAPI.resolve_spark()`
1. Validate spark is active, all produced node IDs exist
2. `store.resolve_spark(spark_id, node_ids, notes, metadata)`
3. Return result

### 11. `get_activity_summary` → `NeoAPI.get_activity_summary()`
1. Parse `since` as ISO datetime (default: 24h ago)
2. `store.get_activity(agent_id, since_dt)`
3. Return structured summary: `{period, counts: {nodes_created, nodes_updated, edges_created, sparks_generated, sparks_resolved}, recent_nodes, active_sparks, contradictions, domains_active}`

---

## MCP Server (`src/neo/mcp/server.py`)

Thin wrapper (~100 lines). Pattern from FastMCP:

```python
from fastmcp import FastMCP
mcp = FastMCP("neo", instructions="Neo semantic memory system.")

@mcp.tool()
async def create_node(node_type: str, title: str, content: str, ...) -> str:
    """Create a new knowledge node."""
    api = await get_api()
    result = await api.store_node(...)
    return json.dumps(result)

# ... additional tools follow the same pattern

if __name__ == "__main__":
    mcp.run()
```

Lazy-init `NeoAPI` singleton via `get_api()`. Auto-creates default agent on first call.

---

## REST API (`src/neo/rest/`)

FastAPI app with equivalent endpoints:

```
POST   /api/nodes                → create_node
GET    /api/nodes/by-title       → find_node_by_title
GET    /api/nodes/{id}           → get_node
GET    /api/nodes/{id}/branch    → get_branch
POST   /api/edges                → link_nodes
PATCH  /api/nodes/{id}           → update_node
POST   /api/search               → search_knowledge
GET    /api/sparks               → get_sparks
POST   /api/sparks/{id}/resolve  → resolve_spark
GET    /api/activity             → get_activity_summary
GET    /api/health               → health check
POST   /api/consolidate          → manual consolidation trigger
```

---

## Consolidation Engine (`src/neo/core/consolidation.py`)

### Triggers (any one)
- Cron schedule (default: every 6 hours, configurable via `NEO_CONSOLIDATION_SCHEDULE`)
- Node threshold (default: 20 new nodes since last pass)
- Manual via CLI `neo consolidate` or REST `POST /api/consolidate`

### Pass 1 — Per-Node (local/fast model, e.g. claude-haiku-4-5)

For each node where `consolidation_version < current_version`:
1. Fetch neighborhood (depth=1)
2. Prompt LLM: given node + neighbors, generate refined NeoLang summary, adjust confidence based on evidence, suggest edge weight changes, flag inconsistencies
3. Apply: update `summary`, `confidence`, edge weights, re-embed if content changed
4. Increment `consolidation_version`
5. Update `skill_notes` on NeoAgent if practical guidance emerged

### Pass 2 — Cross-Node (frontier model, e.g. claude-sonnet-4-20250514)

1. Gather all nodes updated in Pass 1, group by domain
2. Per domain cluster: prompt LLM for synthesis — emergent theories, contradictions, gaps, thin areas
3. Create new `synthesis`-type nodes for significant findings
4. Generate sparks via `SparkGenerator.generate_on_consolidation()`
5. Create edges linking synthesis nodes to constituent knowledge nodes

Returns `{nodes_processed, syntheses_created, sparks_generated, edges_updated}`

### Scheduler (`src/neo/core/scheduler.py`)
- `asyncio.Task`-based tick loop using `croniter`
- Checks both cron schedule and node threshold
- Runs as background task when MCP/REST server starts

---

## Research Drive / Spark Generation (`src/neo/core/sparks.py`)

### At Ingestion (called by `create_node`, background)
1. Fetch top-3 semantically similar existing nodes as context
2. Prompt local model: given new node + context, identify 0-3 sparks (open_question, contradiction, weak_edge)
3. Score priority, create sparks
4. Respect `max_sparks_per_node` (default 3) and `max_sparks_per_day` (default 20) budgets

### At Consolidation (called by Pass 2)
- Additional spark types: `isolated_node`, `thin_domain`
- Generated from cross-node synthesis results

### Priority Scoring
```python
base = {"contradiction": 0.9, "isolated_node": 0.8, "weak_edge": 0.6, "open_question": 0.7, "thin_domain": 0.4}
+ 0.1 if domain in agent.core_domains
+ 0.05 if source node < 24h old
+ 0.1 if source node is isolated (<=1 edge)
- 0.1 if source node is highly connected (>=10 edges)
clamp [0.0, 1.0]
```

---

## Embedding Pipeline (`src/neo/embedding/client.py`)

- **Provider**: OpenAI `text-embedding-3-small` (1536 dims) by default, configurable
- **Single embed**: concatenate `"{title}\n{content}"`, truncate via `tiktoken` if over 8191 tokens, call API
- **Batch embed**: group into batches of 100, single API call per batch, exponential backoff retry
- **SQLite storage**: JSON text in main table + `sqlite-vec` virtual table for search
- **Postgres storage**: `Vector(1536)` column with HNSW index

Reference pattern: existing OpenAI-compatible embedding client with token truncation and batch retry support.

---

## Configuration (`src/neo/config.py`)

Pydantic settings with `env_prefix`:

```
NEO_DB_CONNECTION_URI          default: sqlite+aiosqlite:///neo.db
NEO_DB_SQL_DEBUG               default: false
NEO_EMBEDDING_PROVIDER         default: openai
NEO_EMBEDDING_API_KEY          required for real embeddings
NEO_EMBEDDING_MODEL            default: text-embedding-3-small
NEO_EMBEDDING_DIMENSIONS       default: 1536
NEO_LLM_PROVIDER               default: anthropic
                                supported: anthropic, minimax, openai,
                                openai-compatible, openrouter, ollama,
                                lmstudio, vllm, llama.cpp
NEO_LLM_MODEL                  shared LLM model
NEO_LLM_BASE_URL               shared endpoint
NEO_LLM_API_KEY                shared API key
NEO_LLM_SPARK_*                optional spark-generation overrides
NEO_LLM_RESOLUTION_*           optional spark-resolution overrides
NEO_LLM_CONSOLIDATION_*        optional consolidation overrides
NEO_CONSOLIDATION_SCHEDULE     default: 0 */6 * * *
NEO_CONSOLIDATION_NODE_THRESHOLD default: 20
NEO_CONSOLIDATION_ENABLED      default: true
NEO_AGENT_NAME                 default: default
NEO_LOG_LEVEL                  default: INFO
```

Per-agent overrides stored in `NeoAgent.config` JSON field.

Config precedence:

1. Process environment variables
2. Local checkout `.env`
3. User-level `~/.neo/.env`
4. Built-in defaults

Shared settings such as database, LLM, search, and discovery belong in
`~/.neo/.env`. Agent identity is non-secret and can be supplied with
`NEO_AGENT_NAME` or a launch argument such as `neo serve --agent-name hermes`.
Multiple agent identities share the same Neo network/database while retaining
separate root nodes.

`neo setup` is machine-level only: it writes/updates `~/.neo/.env`, initializes
schema with `init_db()`, and prints MCP launch guidance. It must not create or
configure an agent root. Agent nodes are created lazily when a named agent
connects and can then configure itself conversationally via `configure_agent`.

---

## NeoLang

A writing discipline for agent-readable fields (`summary`, `description`, `skill_notes`, `spark description`). Rules:
- Drop articles, hedging, pleasantries
- Keep technical terms and code exact
- Present tense, omit obvious subjects
- Arrows for causality (`→`), `=` for definitions, `+` for lists
- 40-70% shorter than natural language

**Never** applied to `content` (full language), `title` (clarity), or `source.title` (human-readable).

---

## Testing Philosophy: Test As You Build

**Every piece of code must be tested before moving to the next.** Do not batch implementation — write code, write its test, run the test, fix failures, then move on. This is non-negotiable.

Rules for the implementing agent:
1. **Never write more than one module before testing it.** After creating each `.py` file, write or update the corresponding test and run it.
2. **Run the full test suite after every phase** (`uv run pytest tests/ -v`). Fix regressions before continuing.
3. **If a test fails, fix the code immediately** — do not skip and come back later.
4. **Tests use in-memory SQLite and mock embedding/LLM clients** — no external services needed to run the suite. Tests must be fast and self-contained.
5. **After Phase 4, do a live smoke test** — start the MCP server, call each tool, verify responses make sense.

---

## Implementation Phases

### Phase 1: Foundation

**Build order** (test after each step):

1. Create `pyproject.toml`, `.env.example`, `.gitignore`
2. Create `src/neo/__init__.py`, `src/neo/enums.py`
3. Create `src/neo/config.py` — run `uv run python -c "from neo.config import settings; print(settings)"` to verify import works
4. Create `src/neo/db.py`
5. Create `src/neo/models.py`
6. Create `tests/__init__.py`, `tests/conftest.py` (in-memory SQLite fixture, mock embedding client)
7. Create `tests/test_models.py` — test creating all 5 entity types, constraint validation (confidence/weight ranges), relationship loading
8. **RUN**: `uv run pytest tests/test_models.py -v` — must pass before continuing
9. Set up `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`

### Phase 2: Store Layer

**Build order** (test after each step):

1. Create `src/neo/store/interface.py` (ABC)
2. Create `src/neo/store/sqlite.py` — implement all StoreInterface methods
3. Create `src/neo/store/__init__.py` (factory)
4. Write `tests/test_store_interface.py`:
   - Test all CRUD operations (create/get/update for each entity)
   - Test `vector_search` with deterministic mock embeddings
   - Test `get_neighborhood` graph traversal (create a small graph, verify BFS)
   - Test `get_ancestors` / `get_descendants` (create parent-child chain)
   - Test `get_activity` (create nodes/sparks with timestamps, query by range)
   - Test constraint enforcement (duplicate edges, invalid types)
5. **RUN**: `uv run pytest tests/test_store_interface.py -v` — must pass before continuing
6. Create `src/neo/embedding/client.py`
7. Create `src/neo/store/postgres.py` (can be tested later with real Postgres; skip in CI)
8. **RUN**: `uv run pytest tests/ -v` — full suite, verify no regressions

### Phase 3: Core API + Assembly

**Build order** (test after each step):

1. Create `src/neo/core/__init__.py`
2. Create `src/neo/core/api.py` — the NeoAPI class with all 9 public methods
3. Write `tests/test_api.py`:
   - Test `create_node` creates node with embedding and returns expected fields
   - Test missing parent defaults to agent root
   - Test `link_nodes` creates edge; test `contradicts` auto-generates spark
   - Test `get_node` returns direct-read payload
   - Test `get_branch` returns root plus descendants
   - Test `find_node_by_title` returns ambiguity-aware matches
   - Test `update_node` re-embeds when content changes and validates parent updates
   - Test `search_knowledge` returns ranked results
   - Test `get_sparks` returns prioritized active sparks
   - Test `resolve_spark` links spark to produced nodes
   - Test `get_activity_summary` returns correct counts and structure
4. **RUN**: `uv run pytest tests/test_api.py -v` — must pass before continuing
5. Create `src/neo/core/assembler.py` — the Working Memory Assembly pipeline
6. Write `tests/test_assembler.py`:
   - Test the 6-step pipeline: seed nodes → graph expansion → hierarchy → ranking → compression
   - Test token budget is respected (create many nodes, verify output fits budget)
   - Test contradiction warnings are surfaced
   - Test empty query returns empty results gracefully
7. **RUN**: `uv run pytest tests/test_assembler.py -v` — must pass before continuing
8. Create `src/neo/core/sparks.py` — SparkGenerator + priority scoring
9. Write `tests/test_sparks.py`:
   - Test priority scoring formula produces expected values for each spark type
   - Test domain alignment bonus
   - Test max_sparks_per_node budget enforcement
   - Test spark generation with mocked LLM response
10. **RUN**: `uv run pytest tests/ -v` — full suite, no regressions

### Phase 4: Interfaces (MCP + REST + CLI)

**Build order** (test after each step):

1. Create `src/neo/mcp/__init__.py`, `src/neo/mcp/server.py`
2. Write `tests/test_mcp_server.py`:
   - Test each MCP tool can be called with valid params and returns valid JSON
   - Test error handling for invalid inputs (bad node_type, nonexistent IDs)
3. **RUN**: `uv run pytest tests/test_mcp_server.py -v`
4. Create `src/neo/rest/__init__.py`, `src/neo/rest/app.py`, `src/neo/rest/routes.py`
5. Create `src/neo/cli/__init__.py`, `src/neo/cli/main.py`
6. **SMOKE TEST**: Start MCP server (`uv run python -m neo.mcp.server`), verify it launches without errors
7. **SMOKE TEST**: Start REST server, hit `/api/health`, verify 200 response
8. **RUN**: `uv run pytest tests/ -v` — full suite

### Phase 5: Background Systems

**Build order** (test after each step):

1. Create `src/neo/core/consolidation.py` — ConsolidationEngine with two-pass logic
2. Write `tests/test_consolidation.py`:
   - Test Pass 1: creates nodes, runs per-node consolidation, verify summaries and confidence updated
   - Test Pass 2: verify synthesis nodes created from cross-node pass
   - Test sparks generated during consolidation
   - Test consolidation_version increments
   - All with mocked LLM responses
3. **RUN**: `uv run pytest tests/test_consolidation.py -v`
4. Create `src/neo/core/scheduler.py`
5. **SMOKE TEST**: `uv run neo consolidate` — runs manual consolidation against test DB
6. **FINAL**: `uv run pytest tests/ -v` — entire suite passes, zero failures

---

## Key Architectural Decisions

1. **Store returns dicts, not ORM objects** — keeps interface backend-agnostic, prevents session leaking
2. **Embedding happens in NeoAPI, not store** — store is purely data; API handles embedding calls
3. **Spark generation is fire-and-forget** — `create_node` returns immediately; sparks generated in background task
4. **Consolidation is two-pass** — local model for per-node refinement (cheap), frontier model for cross-node synthesis (targeted)
5. **MCP server is truly thin** — each tool is 5-15 lines: validate, call NeoAPI, serialize
6. **SQLite for local, Postgres for production** — swap via config, not code
7. **Neo Core is pure Python** — no protocol dependencies; MCP and REST are thin layers over the same API

---

## Verification (End-to-End)

After all phases:
1. `uv run pytest tests/ -v` — all tests pass
2. `uv run neo init` — creates database tables
3. `uv run neo serve` — starts MCP server (stdio transport)
4. Connect Claude Code to Neo MCP server, call `find_node_by_title`, `get_node`, `get_branch`, `create_node`, `update_node`, `search_knowledge`, `get_sparks`
5. `uv run neo consolidate` — runs consolidation pass
6. `uv run neo serve-rest` — REST API accessible at `localhost:8420/api/health`

---

## Reference Patterns

- **FastMCP server**: stdio MCP server with tool registration and async lifecycle hooks
- **SQLAlchemy async + pgvector**: async session factory, typed models, and vector columns
- **Embedding client**: OpenAI-compatible embeddings with fallback support
- **Cron scheduler**: async polling scheduler with bounded background tasks
- **Alembic migrations**: standard async SQLAlchemy migration environment
- **Full spec**: `SPEC.md`
