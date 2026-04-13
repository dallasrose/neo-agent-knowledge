# NEO
## Semantic Memory for AI Agents
*Product Specification — Draft v0.3 — April 2026*

---

## 1. What Neo Is

Neo is a semantic memory system for AI agents. Where existing memory systems track what happened — conversations, events, actions — Neo stores what is known: concepts, research findings, and the typed relationships between them.

This is the missing layer. Agents today have episodic memory or none at all. Neo gives them the other kind — the kind that makes expertise possible. Not "we discussed this topic on Tuesday" but "here is everything we understand about this topic, how those ideas connect, where the gaps are, and what to learn next."

Neo is agent-agnostic and memory-system-agnostic. Any agent that can call tools can use Neo. It pairs naturally with episodic systems like Honcho but has no dependency on them.

---

## 2. The Problem

Agents forget everything that isn't in their context window. Current memory systems solve this by storing conversation history and retrieving semantically similar chunks when needed. This works for recall — "what did we discuss about X" — but it doesn't build expertise.

Expertise is different from recall. An expert doesn't just remember past conversations about a topic — they have a structured understanding of it: how concepts relate, where the evidence is strong, where it's contested, what questions remain open. That understanding was built over time through research, synthesis, and consolidation. It lives in semantic memory, not episodic.

Without semantic memory, agents hit a ceiling. They can retrieve what was said but can't reason from accumulated understanding. Every session they start effectively ignorant of their own domain, dependent on whatever context gets injected. They can't develop genuine expertise because there's nowhere for expertise to live.

Neo removes that ceiling.

---

## 3. Core Concepts

Neo is built around five concepts drawn from cognitive science:

### Semantic Memory
The knowledge layer. Concepts, findings, and structured relationships between them. This is what Neo stores and maintains. It doesn't record events — it builds understanding.

### Episodic Memory
The history layer. What happened, when, and to whom. Neo does not own this and does not compete with systems that do. Neo connects to episodic memory systems — Honcho is the primary integration — through lightweight provenance and identity references. The episodic system tracks what the agent has done. Neo tracks what the agent knows. Together they form a complete memory architecture.

### Memory Consolidation
The synthesis process. Periodically, Neo reasons across its full knowledge graph — finding implicit connections, resolving contradictions, updating confidence, identifying gaps. This is the analog to what the brain does during sleep. It's what turns accumulated facts into coherent expertise.

### Working Memory
What's active right now. When an agent recognizes it has relevant domain knowledge mid-task, it pulls from Neo on demand. The agent decides when to retrieve — "I've researched this before, let me look" — and Neo assembles the relevant subgraph in response. Nothing is preloaded. Everything is on demand.

### The Research Drive
Neo doesn't wait to be asked. It maintains a research agenda derived from its own gaps — open questions, unresolved contradictions, weak connections, thin domains. It surfaces this agenda to agents so they can research autonomously and with direction. This is what gives agents a thirst for knowledge rather than passive recall.

These five concepts map to five subsystems in Neo. Together they form a complete cognitive architecture for agents — the same architecture that makes human expertise possible.

---

## 4. Data Model

Neo's data model is minimal by design. The primary graph entities are nodes,
edges, and sparks, plus a NeoAgent record that anchors configuration and
identity. Sources are provenance metadata or optional storage records, not
knowledge parents.

---

### NeoAgent — the identity anchor

```
id
name
specialty            free text — what this agent researches
core_domains         list of root NeoNode IDs or domain names
adjacent_domains     list — related territory, lower research priority
suggested_sources    list of shows, sites, channels, or authors that guide
                     discovery without becoming graph nodes or parents
skill_notes          distilled practical guidance updated by consolidation
                     the bridge between knowing and doing
spark_model          local model endpoint for spark generation
consolidation_model  frontier model endpoint for deep synthesis
max_sparks_per_node  default 3
max_sparks_per_day   default 20
created_at
updated_at
```

NeoAgent is the top-level entity. All nodes, sparks, and configuration belong to an agent. This is also where a future episodic system hooks in — the agent record is the shared identity anchor between Neo and whatever tracks history.

`skill_notes` deserves special attention. As the consolidation pass deepens domain knowledge, it distills practical guidance — not the skill itself, but the best current understanding of how to apply what is known. An agent with rich skill_notes performs better at its specialty without those notes being a skill in the traditional sense. A full procedural memory system — where skills themselves are stored and improved — is future scope.

---

### NeoNode — the unit of knowledge

```
id
agent_id             references NeoAgent
node_type            finding | concept | theory | question |
                     idea | answer | synthesis
parent_id            nullable — references another NeoNode
                     if null, this node is a root
title                short human-readable label
summary              compressed, agent-readable — injected into working memory
content              full research findings, thoughts, questions, conclusions
confidence           0.0–1.0, updated by consolidation passes
embedding            vector representation for semantic search
source_id            nullable — optional provenance record
spark_id             nullable — the spark that prompted this node
created_at
updated_at
last_consolidated_at
```

Node types carry epistemic weight. A `finding` has evidence behind it. A `theory` is explanatory but may be unproven. An `idea` is speculative and not yet researched. An `answer` exists in direct response to a question node. A `synthesis` was generated by consolidation, not direct research. Agents should treat these differently when reasoning from them.

A node represents one thing — one concept, one finding, one theory, one question. If you find yourself writing about multiple things, split them and link the pieces. Size is a signal, not the rule. The hierarchy emerges from `parent_id` relationships. Any node can be a domain — it is just a node that other nodes point to as their parent.

---

### NeoEdge — the typed relationship

```
id
from_node            references NeoNode
to_node              references NeoNode
type                 see relationship vocabulary below
description          free text — the reasoning behind this connection
weight               0.0–1.0, confidence in this relationship
source_id            nullable — optional provenance record
created_at
updated_at
```

#### Relationship Vocabulary

Each type has a distinct meaning. The `description` field carries the nuance when a relationship is complex. If two types seem applicable, the description resolves it.

| Type | Meaning |
|------|---------|
| `supports` | evidence or reasoning that strengthens another node |
| `contradicts` | directly conflicts with another node |
| `prerequisite_for` | must be understood before the other makes sense |
| `extends` | builds on without contradicting |
| `example_of` | concrete instance of an abstract node |
| `questions` | raises doubt or opens inquiry about another node |
| `resolves` | closes or answers a question node |
| `inspired` | loosely sparked, associative rather than logical |
| `connects` | general relationship — use when none above fit |

---

### Provenance

```
metadata.url             source URL when available
metadata.source_title    source title when available
metadata.source_type     youtube | rss | document | manual | ...
source_id                optional shared provenance record for advanced use
```

Sources are not knowledge nodes and not graph parents. The graph structure
models meaning: concepts, findings, theories, syntheses, and their
relationships. Provenance stays on the ingested node as URL/title metadata, with
`source_id` available only when an integration needs a shared provenance record.

---

### NeoSpark — the research drive

```
id
agent_id         references NeoAgent
target_node      nullable — if refining existing knowledge
target_type      open_question | contradiction | weak_edge |
                 isolated_node | thin_domain
description      what specifically needs researching and why
priority         0.0–1.0
source_id        nullable — if a source triggered this spark
status           active | resolved | abandoned
resolved_node_id nullable — the node this spark became
created_at
resolved_at      nullable
```

A NeoSpark is potential energy before a node exists. It is the unformed question, the noticed gap, the unresolved tension. When a spark is researched and findings return, it resolves into one or more NeoNodes. The spark carries the question; the node carries the answer.

Sparks persist in the graph after resolution. A resolved spark linked to its node is intellectual history — it shows what was unknown, what was asked, and what was found. Abandoned sparks are questions that turned out to be wrong questions or were superseded. All of it is worth keeping.

Sparks are generated at two moments: during ingestion, when the local model finds gaps in new content; and during consolidation, when cross-node reasoning surfaces new questions. This mirrors how humans think — new knowledge immediately raises questions, and synthesis raises deeper ones.

New ideas do not need to start as sparks. A fully formed idea can be stored directly as an `idea`-type node. A half-formed hunch is better started as a spark and developed through research. The agent decides.

---

### Indexes

```
Vector index     node embeddings — semantic search
Standard         node_type, parent_id, agent_id, confidence
                 updated_at, last_consolidated_at
                 edge type, weight
                 spark status, priority, resolved_at
```

---

### Episodic Boundary

Neo does not store sessions or events. Provenance metadata records where
knowledge came from. The episodic system owns session history. Neo just knows
what it learned and from where. These are different things.

---

## 5. API and Tool Surface

Neo exposes a small MCP tool surface. Agents call these during research sessions and on demand during tasks. The tools handle all storage, embedding, indexing, and spark generation internally — the agent thinks in knowledge, not infrastructure.

---

### `create_node`
*Create a new unit of knowledge.*

```
title          what this is about
node_type      finding | concept | theory | question |
               idea | answer | synthesis
content        full findings, thoughts, conclusions
summary        compressed version for context injection
parent_id      optional — where this sits in the hierarchy
source_id      optional — where this came from
spark_id       optional — the spark that prompted this research
metadata       optional — provenance such as source URL and source title
```

Returns the new node ID. If `parent_id` is omitted, Neo stores the node under the agent root. Automatically generates an embedding, schedules consolidation, and runs local model inference to generate NeoSparks for questions or gaps detected in the content — subject to per-node and per-day spark budget configuration.

`store_node` remains as a compatibility alias, but new integrations should use `create_node`.

---

### `link_nodes`
*Create a typed relationship between two nodes.*

```
from_node      node ID
to_node        node ID
type           relationship type from vocabulary
description    the reasoning behind this connection
weight         0.0–1.0, confidence in this link
source_id      optional
```

If type is `contradicts`, automatically generates a NeoSpark of type `contradiction` and flags both nodes for consolidation.

---

### `update_node`
*Refine existing knowledge.*

```
node_id        which node to update
content        new or revised content
summary        updated summary
confidence     revised confidence
parent_id      optional — move the node under a different knowledge parent
metadata       optional — merged metadata updates
```

Triggers re-embedding when content changes and reschedules consolidation. Preserves prior content in history so the consolidation pass can reason about how understanding evolved. Parent updates are validated so nodes cannot be moved under themselves or descendants.

---

### `get_node`
*Read one known node directly, with local graph context.*

```
node_id            which node to read
include_edges      optional — defaults true
include_ancestors  optional — defaults true
include_children   optional — defaults false
```

Returns the node itself plus optional local context — direct edges, parent chain, and immediate children. This is the tool agents use when they already know the anchor they want to read, such as a root domain or policy node.

---

### `get_branch`
*Read a node and its descendant branch directly.*

```
root_node_id       which node anchors the branch
max_depth          optional — how far down to traverse, default 2
include_edges      optional — defaults true
```

Returns the root node, descendant nodes, and internal branch edges. This is the tool for instructions like "read the Agents branch before working" — ontology roots, playbooks, policy trees, and structured concept hierarchies.

---

### `find_node_by_title`
*Resolve a human-readable title into one or more candidate nodes.*

```
title              title to look up
exact              optional — defaults true
domain             optional — restrict to one domain
limit              optional — defaults 10
```

Returns `selected_match`, `matches`, `count`, and `ambiguous`. This is the tool for prompts like "read the top node Agents" when the caller knows a title but not an ID. If multiple nodes share the same title, Neo returns all matching candidates in ranked order instead of guessing silently.

---

### `search_knowledge`
*Find relevant nodes by semantic similarity and graph proximity. Called on demand when the agent recognizes it has relevant domain knowledge — not at session start.*

```
query          natural language description of what's needed
parent_id      optional — constrain to a branch of the hierarchy
node_types     optional — filter by type
top_k          initial retrieval count, default 10
hop_depth      edge traversal depth from results, default 2
min_weight     minimum edge weight for traversal, default 0.5
token_budget   safety ceiling on returned context, default 2000
```

The index does the primary work — semantic search narrows to the most relevant nodes before anything expensive happens. Graph traversal follows only strong edges from those results. The token budget is a safety valve. If it is consistently being hit, the search query needs to be more specific. Returns ranked node summaries with confidence scores. Nodes with open contradictions are surfaced with a warning.

---

### `get_sparks`
*Retrieve the current research agenda.*

```
parent_id      optional — constrain to a branch of the hierarchy
target_types   optional — filter by spark type
status         optional — active | resolved | abandoned
               defaults to active
limit          how many sparks to return, default 5
min_priority   optional — priority threshold
```

Returns prioritized NeoSparks with descriptions. This is what the research drive calls to know what to work on next. Resolved and abandoned sparks are available for historical queries — they represent the intellectual history of the knowledge base.

---

### `resolve_spark`
*Mark a spark as resolved and link it to what it produced.*

```
spark_id       which spark was resolved
node_ids       one or more nodes this spark became
notes          optional — what was found, what remains open
```

The spark persists in the graph with status `resolved`, linked bidirectionally to the nodes it produced. If researching the spark opened new questions, store those as question-type nodes before resolving — they will generate new sparks automatically. A spark can also be marked `abandoned` if it was superseded or turned out to be the wrong question.

---

### `get_activity_summary`
*Return structured activity since a given timestamp. The raw material for the daily brief.*

```
since          timestamp — how far back to look, default 24 hours
agent_id       which agent's activity to summarize
include        optional list — 'nodes' | 'edges' | 'sparks' |
               'contradictions' | 'consolidation' | 'sources'
               defaults to all
```

Returns:

```
nodes_created        new nodes with titles, types, confidence
nodes_updated        significantly changed nodes, what changed
edges_created        new relationships established
sparks_generated     new sparks with descriptions and priority
sparks_resolved      sparks that became nodes overnight
sparks_abandoned     questions that were closed without resolution
contradictions        open conflicts flagged, unresolved count
consolidation_insights what the synthesis pass surfaced
sources_added        new sources ingested
```

*Neo supplies the data. The agent shapes it. `get_activity_summary` is the interface between them — structured, timestamp-scoped, complete. What the agent does with it is its own concern.*

---

### The Daily Brief — Agent-Layer Pattern

`get_activity_summary` is designed to power a daily brief. The pattern:

```
1. Agent calls get_activity_summary(since='24h')
2. Agent reads structured activity against your current context —
   your projects, priorities, what you were working on yesterday
3. Agent produces a human-readable brief covering:

   Overnight research     what was investigated, what was found
   New knowledge          nodes created, significant updates
   Open questions         new sparks, priority order
   Contradictions         unresolved conflicts needing attention
   Consolidation insights what synthesis surfaced
   Recommended focus      top 3 things worth your attention today

4. Delivered however you want — first message of the day,
   email, notification
```

The recommended focus is the agent's contribution. Neo knows what changed. The agent knows what matters to you. The brief is where those two things meet.

---

### Configuration

```
max_sparks_per_node     max sparks generated per create_node call, default 3
max_sparks_per_day      daily spark generation ceiling, default 20
suggested_sources       optional shows, sites, channels, or authors that guide
                        discovery without becoming graph nodes or parents
spark_model             local model endpoint — spark generation,
                        embedding, contradiction detection
consolidation_model     frontier model endpoint — deep consolidation,
                        multi-hop synthesis, contradiction resolution
```

The agent-facing surface is intentionally small. Consolidation, embedding, graph traversal, and spark generation all happen in the background. The agent can now read known nodes and branches directly, resolve titles into IDs, and then research, write, update, or link from there. Neo handles everything else.

---

## 6. The Five Subsystems

Each subsystem maps to one of the five core concepts. These are internal architecture — not part of the agent-facing surface.

---

### 6.1 The Semantic Store

The foundation. Everything else reads from and writes to it.

The semantic store is a graph database with vector search. NeoNodes are vertices. NeoEdges are directed, typed edges. NeoSources and NeoSparks are first-class records. The hierarchy is expressed through `parent_id` — there is no separate schema for domains or categories. The graph is the data model.

Storage is PostgreSQL with pgvector for production deployments. SQLite with sqlite-vec for local and embedded deployments. The choice is configuration, not architecture — the store interface abstracts both.

```python
StoreInterface
    write_node(node)         → node_id
    write_edge(edge)         → edge_id
    write_spark(spark)       → spark_id
    write_source(source)     → source_id
    update_node(id, delta)   → node
    get_node(id)             → node
    get_edges(node_id)       → edges
    get_activity(agent_id, since, include)         → activity_summary
    vector_search(embedding, filters, top_k)       → nodes
    traverse(node_ids, hop_depth, min_weight)      → nodes
```

Any storage backend that implements this interface works with Neo. PostgreSQL and SQLite ship as defaults. Others can be added without touching any other subsystem.

---

### 6.2 Working Memory Assembly

On-demand knowledge retrieval. The agent pulls when it recognizes relevance — not at session start.

When an agent calls `search_knowledge`, the assembly process runs:

```
1. Semantic search
   Query vector index with the agent's search query
   Return top_k most relevant nodes by embedding similarity

2. Graph expansion
   Traverse edges from results up to hop_depth
   Follow only edges with weight >= min_weight
   Prioritize supports and prerequisite_for edges
   Flag any contradicts edges encountered

3. Hierarchy surfacing
   Walk parent_id chain for each result node
   Include parent summaries for context
   Do not include full parent content — summaries only

4. Ranking
   Score: embedding similarity + confidence
          + recency of last consolidation
          - penalty for open contradictions

5. Compression
   Take ranked nodes in order until token budget is reached
   Use summary field, not content field
   Append contradiction warnings
   Append relevant active NeoSparks

6. Return
   { knowledge, contradictions, sparks }
```

The agent gets structured context it can reason from immediately. The index does the filtering. The token budget is a ceiling. Depth of retrieval is controlled by the quality of the search query.

---

### 6.3 Memory Consolidation

The sleep cycle. Turns accumulated knowledge into coherent expertise.

Consolidation runs in the background on a schedule and on triggers. It is the most important subsystem and is designed to be cheap by default — the local model does the per-node work, the frontier model is used only for targeted cross-node synthesis. Most consolidation passes touch only a small subset of the graph.

> **Note:** Consolidation elegance is an active design concern. The per-node local model pass likely handles the majority of cases. The frontier model cross-node pass should be increasingly selective as the graph matures. Design for cheapness from the start.

#### Triggers

```
Scheduled        every N hours, configurable per agent
Node threshold   when M new nodes created since last pass
Manual           agent or developer can trigger explicitly
```

#### Consolidation Pass

```
1. Collect candidates
   Nodes updated since last_consolidated_at
   Nodes with open contradiction sparks
   Isolated nodes with no edges

2. Per-node pass  (local model)
   Update summary if understanding has deepened
   Recalculate confidence from edge weights
   Flag nodes that should be split
   Generate sparks for emerging questions
   Update skill_notes on NeoAgent if practical guidance has changed

3. Cross-node pass  (frontier model — targeted)
   Reason across candidate nodes together
   Find implicit connections not yet in edges
   Propose new edges with types and descriptions
   Identify patterns across multiple findings
   Surface simplest explanations for patterns
   Resolve contradictions where evidence is sufficient

4. Write results
   New edges from cross-node pass
   Updated summaries and confidence scores
   New sparks from gaps found
   Updated skill_notes on NeoAgent
   Update last_consolidated_at on all processed nodes
```

The frontier model only touches nodes that genuinely need cross-node synthesis — a small, curated subset that the local model has already prepared. This is where cost is controlled.

---

### 6.4 The Research Drive

The thirst. What makes Neo an active cognitive system rather than a passive store.

#### Spark Generation

Sparks are generated at two moments:

```
Ingestion       source ingestion extracts durable learnings, then create_node triggers local model inference
                finds gaps, questions, tensions in new content
                subject to max_sparks_per_node budget

Consolidation   cross-node pass surfaces new questions
                as understanding deepens, new unknowns appear
                this is where the deeper sparks come from
```

This mirrors human cognition. New knowledge immediately raises questions. Synthesis raises deeper ones.

#### Priority Scoring

| Target Type | Priority | Reason |
|-------------|----------|--------|
| `contradiction` | Highest | Unresolved conflicts degrade all dependent knowledge |
| `isolated_node` | High | Unconnected knowledge isn't integrated into the graph |
| `weak_edge` | Medium | Connections below 0.4 need strengthening or removal |
| `open_question` | Medium | Questions the agent explicitly flagged |
| `thin_domain` | Lower | Broad coverage gaps, longer horizon |

#### The Curiosity Loop

```
get_sparks
    → pick highest priority spark
    → research it
    → create_node (findings)
    → link_nodes (connections)
    → resolve_spark
    → new sparks generated automatically
    → loop
```

Following connections is what generates organic curiosity. A node about one concept links to a related concept which has a weak edge to a third. That weak edge is a spark. The agent follows it. New nodes, new edges, new sparks. The graph grows in the direction of its own gaps.

#### Domain Anchoring

Unbounded curiosity drifts. The research drive respects the domain anchor defined in NeoAgent. Sparks that would pull research outside the agent's core and adjacent domains are deprioritized, not deleted — they may become relevant later. Recommended sources in NeoAgent give the research drive trusted starting points for new investigations.

---

### 6.5 The MCP Interface

The transport layer. How agents and external systems talk to Neo.

Neo's core knows nothing about MCP. The MCP interface is a thin wrapper over Neo's Python API — it translates MCP tool calls into API calls and returns results in MCP format. If MCP is replaced by a better protocol, this layer is swapped without touching anything else.

```
Neo Core  (pure Python, no protocol dependencies)
    ↓
Neo Python API  (clean interface, fully usable standalone)
    ↓
MCP Server  (thin wrapper, ~100 lines)
```

The tools from Section 5 are exposed directly as MCP tools. No transformation of semantics — tool names, parameters, and return values mirror the Python API exactly. Compatibility aliases may remain temporarily, but documentation and new integrations should prefer the current names.

MCP handles the agent-facing interface. Consolidation, spark generation, and background processing run on a scheduler — they are not triggered by tool calls and are not part of the MCP surface.

Additional interfaces that ship alongside MCP:

```
REST API         for non-MCP integrations and dashboard access
Python library   for direct integration without a server
```

---

## 7. Pluggability

Neo is designed to plug into existing agent infrastructure, not replace it. Three seams define where Neo connects to the outside world: above (agents), below (storage), and alongside (episodic memory).

### Above — Agent Interface

Any agent that can call tools can use Neo. The MCP server is the primary interface. The Python library is available for direct integration. No assumptions are made about the agent framework, model provider, or orchestration layer.

### Below — Storage Interface

The StoreInterface defined in Section 6.1 abstracts all storage. PostgreSQL with pgvector ships as the production default. SQLite with sqlite-vec ships for local and embedded use. Any backend that implements StoreInterface works with Neo.

Neo can run fully locally — no cloud dependencies, no external services. Everything on the machine. For agents handling sensitive domain knowledge, this matters.

### Alongside — Episodic Memory

Neo does not own episodic memory. It connects to episodic systems through two lightweight mechanisms:

```
Provenance       node metadata — URL, source title, source type,
                 conversation ID, session ID, or any external identifier

NeoAgent         identity anchor — shared reference point
                 between Neo and the episodic system
```

Honcho is the recommended episodic partner. A Honcho peer and a NeoAgent share an identity. Honcho tracks what the agent has done. Neo tracks what the agent knows. Together they form a complete memory architecture.

Neo will ship its own episodic layer in a future version, designed to fit the semantic layer exactly. The episodic interface will remain open. Honcho and other systems will continue to work.

### Versioning and Stability

The MCP/API surface is stable. Internal subsystems — consolidation logic, spark generation, embedding strategy — will evolve. The StoreInterface is stable. Model configuration is per-agent and does not affect the interface.

---

## 8. NeoLang

NeoLang is the compressed, information-dense language Neo uses for all agent-readable fields. It is not a programming language. It is a writing discipline — a set of rules for how agents write summaries, edge descriptions, spark descriptions, and skill_notes so that maximum signal fits in minimum tokens.

Agents don't need pleasantries. They need dense accurate signal.

### Rules

| Rule | Example |
|------|---------|
| Drop articles (a, an, the) | "neural net overfit small dataset" not "a neural network will overfit on a small dataset" |
| Drop pleasantries and hedging | never "it might be worth considering" |
| Keep technical terms exact | `polymorphism` stays `polymorphism`, `backpropagation` stays `backpropagation` |
| Keep code exact | code blocks written normally |
| Use present tense | "gradient descent minimize loss" not "gradient descent minimizes loss" |
| Omit subjects when obvious | "minimizes loss function" not "this finding minimizes the loss function" |
| Use arrows for causality | "high learning rate → unstable training → divergence" |
| Use `=` for definitions | "overfitting = model memorize train data, fail generalize" |
| Compress lists with `+` | "dropout + weight decay + early stopping = effective regularization" |

### Where NeoLang Applies

```
summary          ✓ NeoLang — agent-readable, injected into context
description      ✓ NeoLang — edge reasoning, agent-readable
skill_notes      ✓ NeoLang — practical guidance, agent-readable
spark desc       ✓ NeoLang — research agenda, agent-readable

content          ✗ Full language — source of truth, human + consolidation readable
title            ✗ Full language — clarity over compression
source titles    ✗ Full language — human readable
```

`content` is never NeoLang. It is the full record — the research, the nuance, the context. Consolidation reasoning depends on it. NeoLang is only for the fields that get injected into agent context repeatedly. Compress the summaries. Preserve the content.

### Token Impact

A well-written NeoLang summary is 40–70% shorter than natural language with no loss of technical substance. Across a working memory assembly of 10–20 nodes, this is the difference between fitting in a 2,000 token budget and blowing past it.

---

## 9. What Neo Is Not

| Neo is not | Notes |
|------------|-------|
| An episodic memory system | Neo does not store what happened. It stores what is known. Episodic memory is well-covered — Neo connects to it, not competes with it. |
| A RAG pipeline | Neo reasons about knowledge and maintains a typed graph. It is not a vector store with a retrieval layer on top. |
| A skill system | Procedural memory is well-covered — Neo informs skills via `skill_notes` but does not own them. Neo connects to skill systems, it does not replace them. |
| A document store | NeoSource can optionally store full content, but Neo is designed for structured understanding derived from documents, not document retrieval. |
| A general knowledge base | Neo is scoped to an agent's specialty via domain anchoring. It is not designed to hold all human knowledge. |
| A walled garden | Neo owns the semantic layer and connects cleanly to everything else. The episodic system, the skill system, the agent framework — those are yours to choose. |

---

## Roadmap

### v1 — Semantic Layer
Neo ships as the semantic memory layer. MCP interface. PostgreSQL and SQLite backends. Local model spark generation. Frontier model consolidation. NeoAgent configuration. NeoLang for agent-readable fields. Source provenance stays as metadata on knowledge nodes; suggested sources live in agent config.

### v1.5 — NeoVis
Neo ships a graphical UI for exploring and auditing the knowledge graph.

**Graph view** — the primary interface. Nodes as orbs, colored and sized by type and confidence. Sparks as glowing proto-nodes. Resolved sparks dimmed but visible, showing lineage. Active sparks pulsing. Edges labeled by relationship type on hover. The graph makes the knowledge base legible at a glance and serves as a development and debugging tool — isolated nodes, contradiction clusters, and consolidation results are all visible.

```
findings     blue
concepts     white
theories     purple
questions    yellow
ideas        orange
answers      green
synthesis    teal
sparks       yellow, glowing, smaller
```

**Hierarchical text view** — a tree panel alongside the graph. Parent nodes expand to children. Summaries visible inline. Full content on click. Browsable by humans the way a knowledge base should be.

**Direct chat** — optional, for inspection and debugging. Talk to Neo directly without an agent. Useful for auditing what the graph knows without going through a full agent session.

Stack: React + Three.js for the graph. REST API as the backend. Ships as a web UI for hosted deployments, Electron for local.

### v2 — Knowledge Base Import
Neo ships importers for existing knowledge bases. Obsidian is the primary target — wikilinks become `connects` edges, frontmatter becomes metadata, folder hierarchy becomes `parent_id` structure. A lightweight inference pass upgrades untyped wikilinks to typed relationships over time via consolidation. Additional targets: Notion, Roam Research, LogSeq, plain markdown folders. This is the adoption unlock — millions of users have already built the knowledge, Neo just needs to ingest it.

### v3 — Integration Layer
Neo deepens its connections to the episodic and procedural memory systems agents are already using. Episodic and procedural memory are well-covered territory — Neo does not compete with them. It connects to them cleanly.

Episodic integrations: Honcho (primary), mem0, and others via a standard episodic adapter interface. Neo surfaces relevant semantic knowledge in response to episodic events. Episodic systems can write research events directly into Neo's provenance model.

Procedural integrations: agent skill systems, tool registries, and workflow engines. Neo's `skill_notes` become a feed into procedural systems — consolidated domain knowledge informing how skills are defined and updated, without Neo owning the skills themselves.

The goal: Neo as the semantic layer any cognitive architecture can plug into, regardless of what handles episodic and procedural.

---

*— end of draft v0.3 —*
