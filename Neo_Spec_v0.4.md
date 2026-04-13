# NEO
## Semantic Knowledge Graph for AI Agents
*Product Specification — v0.4 — April 2026*

---

## 1. What Neo Is

Neo is the semantic memory layer for AI agents. Where episodic memory systems track what happened — conversations, events, preferences — Neo stores what is known: structured research, synthesised findings, and the typed relationships between them.

This is the missing layer. Agents with only episodic memory start every session effectively ignorant of their own domain. They can recall past conversations but cannot reason from accumulated understanding. Neo gives them expertise — not "we discussed this on Tuesday" but "here is everything understood about this topic, how the ideas connect, where the evidence is strong, and what questions remain open."

**Neo is the research layer. Memory systems are the index into it.**

When an episodic system surfaces "you've researched this before," the agent goes to Neo for the actual knowledge. Neo builds autonomously in the background; agents query it on demand. The two systems compose naturally without either knowing about the other — through the agent, not through integration code.

Neo is agent-agnostic. Any agent that supports MCP can use it. It pairs naturally alongside episodic systems like Mem0 and Honcho but has no dependency on them and does not compete with them.

---

## 2. Node Types

Six types. The first two are reserved — Neo creates and manages them. The last four are what agents work with.

| Type | Reserved | Meaning |
|---|---|---|
| `container` | ✓ | Structural scaffolding — domain roots, organisational nodes |
| `agent` | ✓ | The agent itself — identity anchor |
| `concept` | | Named knowledge thing — definition, model, category, mental model |
| `finding` | | Observed fact or evidence-backed conclusion |
| `theory` | | Explanatory claim about how or why something works |
| `synthesis` | | Conclusion produced by consolidating multiple nodes |

Type carries epistemic weight. A `finding` has evidence. A `theory` is explanatory but may be unproven. A `synthesis` was produced by the consolidation pass, not direct research. Agents reason differently from each.

One node = one thing. If a node covers multiple ideas, split it.

---

## 3. Data Model

### Node

```
id
agent_id             which agent owns this node
node_type            see above
parent_id            nullable — hierarchy anchor
status               active | consolidated
title                short, descriptive, searchable
summary              compressed NeoLang — returned in search results
content              full record — source of truth, never compressed
confidence           0.0–1.0 — updated by consolidation
embedding            vector for semantic search
domain               optional label for broad grouping
source_id            nullable — provenance
spark_id             nullable — the spark this resolved
created_at
updated_at
last_consolidated_at
```

### Edge

```
id
from_node_id
to_node_id
edge_type            supports | contradicts | prerequisite_for |
                     extends | example_of | questions |
                     resolves | inspired | connects | parent
description          NeoLang — the reasoning behind the connection
weight               0.0–1.0 — confidence in this relationship
created_at
```

### Spark

```
id
agent_id
target_node_id       nullable — which node raised this question
spark_type           open_question | contradiction | weak_edge |
                     isolated_node | thin_domain
description          what specifically needs investigating
priority             0.0–1.0
status               active | resolved | abandoned
resolved_node_id     nullable — the node the spark became
notes                nullable — what was found
created_at
resolved_at
```

### Agent

```
id
name
specialty            what this agent researches
config               max_sparks_per_node, max_sparks_per_day,
                     root_node_id, suggested_sources, etc.
created_at
```

---

## 4. The Index

The index is the graph itself. There is no separate indexing pipeline.

Embeddings are computed and stored on every `create_node` and content-changing
`update_node` call. The vector index is always current. Quality is reflected in
node metadata that accumulates through normal operations — `confidence`,
`status`, `last_consolidated_at` — and applied dynamically at query time.

**`search_knowledge` constructs a clean view on demand:**

```
1. Vector search — semantic similarity against query embedding
2. Filter — exclude status:consolidated, low confidence orphans
3. Graph expansion — traverse edges up to hop_depth, min_weight threshold
4. Rank — similarity × confidence, penalise open contradictions
5. Compress — summaries only, token budget ceiling
6. Return — ranked nodes + contradiction warnings + relevant active sparks
```

No maintenance job. When consolidation creates a synthesis from three findings, it marks those findings `status: consolidated` at write time. They disappear from default search results immediately. Quality is a write-time concern, not a scheduled scan.

---

## 5. Background Jobs

Two jobs. Both run inside the `neo serve` process on configurable schedules.

### Contemplation Loop

Runs on an interval (default: configurable minutes).

Scans for structural signals — recently added nodes and isolated nodes with no sparks. For each candidate, calls the spark LLM to generate 0–3 sparks representing genuine gaps, tensions, or open questions. Biased toward returning zero — only emits a spark if it is genuinely worth an agent's time.

This is not automated research. It is automated noticing. The agent does the research.

### Consolidation Scheduler

Runs on a cron schedule and on a node-count threshold.

```
1. Collect candidates
   — nodes updated since last_consolidated_at
   — nodes with open contradiction sparks
   — isolated nodes with no edges

2. Per-node pass (fast model)
   — update summary if understanding has deepened
   — recalculate confidence from edge weights
   — generate sparks for emerging gaps

3. Cross-node pass (capable model — targeted)
   — find implicit connections across candidates
   — propose new typed edges
   — resolve contradictions where evidence is sufficient
   — produce synthesis nodes where warranted
   — mark constituent nodes status:consolidated

4. Write results
   — new edges, updated confidence, new sparks
   — synthesis nodes linked to constituents
   — update last_consolidated_at
```

The capable model only touches a small, curated subset the fast model has already prepared. Cost is controlled by selectivity, not by using a cheaper model for everything.

---

## 6. Spark Lifecycle

```
create_node()
    → contemplation loop notices structural gap
    → spark generated with description and priority
    → agent calls get_sparks() → sees agenda
    → agent investigates (reads neighbours, may debate sub-agent)
    → agent stores or updates a node with settled insight
    → agent calls resolve_spark(node_id)
    → node gains spark count → visualiser blends toward gold
    → consolidation eventually synthesises related findings
```

Spark resolution is always agent-driven. It requires reasoning, web research, and judgment. The contemplation loop generates the agenda. The agent executes it.

---

## 7. MCP Interface

Neo exposes a stdio MCP server (`neo serve`). Spawned on-demand by the agent framework as a subprocess — no persistent daemon, no separate service to manage.

### Tools

| Tool | Purpose |
|---|---|
| `get_neo_guidance` | Explain Neo's tool workflows to an agent |
| `create_node` | Create a node — embeds, generates sparks, defaults to the agent root if parent_id is omitted |
| `store_node` | Compatibility alias for create_node |
| `update_node` | Refine existing knowledge, metadata, or parent_id — re-embeds when content changes |
| `delete_node` | Remove a node and cascade to orphaned children |
| `get_node` | Read one node with edges, ancestors, optional children |
| `get_branch` | Read a node and its descendant tree |
| `find_node_by_title` | Resolve a title to node ID — call before create_node |
| `link_nodes` | Create a typed edge between two nodes |
| `search_knowledge` | Semantic search with graph expansion — the primary retrieval tool |
| `get_sparks` | Retrieve prioritised research agenda |
| `resolve_spark` | Close a spark, link to the node it produced |
| `abandon_spark` | Close a spark that was a false positive |
| `get_activity_summary` | Structured activity since a timestamp |
| `configure_agent` | Set research direction, domains, skill notes, and suggested source hints |
| `trigger_discovery` | Run discovery immediately for the current research direction |
| `ingest_youtube` | Ingest a YouTube transcript as distinct finding nodes |

Sources are not graph parents. Source hints live on the agent config as
`suggested_sources` and guide discovery. Ingested nodes keep provenance in
metadata, including source URL and source title, while parent/edge structure
models the knowledge itself. A single source can produce multiple finding nodes
when it contains multiple durable learnings.

### Prompt Resource

`neo_usage_guidance` — a prompt resource the agent loads on connect. Contains the node taxonomy, usage rules, and the spark resolution protocol. This is the canonical source of operational instructions. Tool docstrings reinforce the same rules so they are present at the moment of each tool call.

**The `get_sparks` docstring carries the resolution protocol.** Every call to get_sparks delivers the protocol regardless of whether the agent has loaded the prompt resource.


## 8. REST API

Mirrors the MCP tool surface. Primary consumer is the Neo visualiser.

```
GET    /health
POST   /nodes
GET    /nodes
GET    /nodes/:id
PATCH  /nodes/:id
DELETE /nodes/:id
PATCH  /nodes/:id/parent
GET    /nodes/:id/branch
GET    /nodes/by-title
POST   /edges
POST   /search
GET    /sparks
POST   /sparks/:id/resolve
GET    /activity
GET    /graph                  full graph snapshot for visualiser
POST   /consolidate            manual consolidation trigger
```

---

## 9. Multi-Agent

Each agent writes to its own namespace — all nodes carry `agent_id`. Any agent can read any other agent's nodes. There is no private/public distinction: the graph is open within an installation.

`search_knowledge` accepts a `scope` parameter:
- `self` — own nodes only (default)
- `network` — all agents in the installation

This is the network model. Agents share a knowledge graph. Each owns what it writes. All can read what others have built.

---

## 10. Visualiser

React frontend. Force-directed graph via `react-force-graph-3d`.

**Node appearance:**
- Size by hierarchy depth — root nodes (agent, container) are large; leaves are small
- Color by type — see palette below
- Color blends toward gold as resolved sparks accumulate on a node (cumulative: each resolved spark pushes 30% further toward `#c8960c`)
- Active sparks rendered as amber orbs

**Node color palette:**
```
container   #475569   slate — muted scaffolding
agent       #d946ef   neon purple — identity
concept     #93c5fd   light blue — named knowledge
finding     #2563eb   solid blue — observed fact
theory      #ea580c   orange — explanatory claim
synthesis   #00e5cc   neon teal — consolidated conclusion
spark       #fbbf24   amber — potential energy
```

**Panels:**
- Left: hierarchical tree view
- Right: node or spark detail panel with edges and metadata
- Bottom left: legend
- Filter bar: type visibility and text search

---

## 11. Installation Tiers

```
pip install neo-agent-knowledge SQLite + MCP + REST + visualiser
                                mock embeddings (hash-based, no API key)
                                no spark generation
                                fully functional, reduced search quality

pip install neo-agent-knowledge[embeddings]
                                adds openai SDK
                                real semantic search via any OpenAI-compatible
                                embedding endpoint
                                set NEO_EMBEDDING_API_KEY

pip install neo-agent-knowledge[sparks]
                                adds anthropic SDK
                                auto spark generation via any
                                Anthropic-compatible endpoint
                                set NEO_LLM_SPARK_API_KEY

pip install neo-agent-knowledge[postgres]
                                adds psycopg + pgvector
                                production-scale storage backend
                                set NEO_DATABASE_URL

pip install neo-agent-knowledge[all]
                                everything
```

Zero-config minimum works out of the box. Each capability unlocks with one env var.

---

## 12. Agent Integration

**Connect:** add `neo serve` to the agent's MCP server config. Neo starts automatically on first tool call.

**System prompt / SOUL.md:** one instruction does the work:

> Before researching a topic or answering a knowledge-heavy question, call `search_knowledge` on Neo first.

Agents can set a research direction with `configure_agent`. Named shows, sites,
channels, or authors belong in `suggested_sources`; they guide discovery but do
not become knowledge nodes or parents.

This is the weakest link in the system — instruction following degrades with context length and depends on the agent. It is the right tradeoff: no per-turn token overhead, no dependency on memory system APIs, works with any agent framework. Reliability improves as models improve at instruction following.

**Memory system:** no configuration needed in Neo. Neo builds the knowledge graph autonomously in the background. The episodic memory system handles its own layer independently. They coexist through the agent — not through integration code.

---

## 13. What Neo Is Not

| Neo is not | Why |
|---|---|
| An episodic memory system | Neo stores what is known, not what happened. Episodic memory is well-covered. Neo connects to it, not competes. |
| A RAG pipeline | Neo maintains a typed knowledge graph with confidence, relationships, and autonomous consolidation. Not a vector store with retrieval on top. |
| A memory injection system | Neo does not push into or integrate with episodic memory systems. It builds autonomously; agents pull from it when relevant. |
| A replacement for Mem0 / Honcho | Different layer entirely. They handle episodic. Neo handles semantic. Both together is the complete architecture. |
| A document store | Neo stores structured understanding derived from documents, not the documents themselves. |

---

## Roadmap

**v1 — Core**
MCP server. SQLite backend. Contemplation loop. Consolidation. Spark lifecycle. REST API. Install tiers.

**v1.5 — Visualiser**
Neo ships with the visualiser. Full graph view, tree panel, spark panel, filter bar. Runs alongside `neo serve`.

**v2 — Quality**
Confidence-weighted consolidation. Near-duplicate detection. NeoLang enforcement. Source concentration warnings. Better spark prioritisation.

**v3 — Scale**
PostgreSQL backend. Multi-agent network features. Knowledge import (Obsidian, Notion, markdown). Direct episodic system integrations for agents that want tighter coupling.

---

*— v0.4 —*
