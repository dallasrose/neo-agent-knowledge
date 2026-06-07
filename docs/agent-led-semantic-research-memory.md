# Agent-Led Semantic Research Memory Plan

Status: active implementation note  
Last updated: 2026-04-29

## Product Frame

Neo is an implementation detail: a semantic memory and knowledge-graph substrate. The agent should experience and describe the contents as its own research memory, knowledge, notes, and expertise.

User-facing language should be first-person and natural:

- "I remember researching this."
- "I have notes on this."
- "This connects to something I learned earlier."
- "Let me pull from my research memory."

Avoid making Neo sound like a separate character that learns or decides. Tool/debug language may still mention Neo as the storage/retrieval system.

## Current Progress

### Neo memory provider for Hermes

Done:

- Neo has a Hermes memory-provider integration package.
- The provider exposes compact automatic semantic signals and explicit tools:
  - `neo_search`
  - `neo_remember`
  - `neo_get_node`
  - `neo_sparks`
- Automatic recall is signals-first and avoids raw graph dumps.
- Neo provider config lives in `$HERMES_HOME/neo.json`.

### Hermes multiple memory providers

Done in local Hermes checkout:

- `memory.provider` remains the legacy single-provider config.
- `memory.providers` opts into multiple external providers in configured order.
- Default `MemoryManager()` behavior still rejects a second external provider.
- `MemoryManager(allow_multiple_external=True)` accepts multiple providers and preserves first-provider tool routing on conflicts.
- Tests cover legacy and plural behavior.

### Existing Neo ingestion/research machinery

Already present:

- `DiscoveryJob` can poll configured research sources and run autonomous search.
- Supported source types include YouTube channel/feed/search and RSS.
- YouTube transcript fetching and relevant excerpt extraction exist.
- `extract_knowledge_findings()` extracts durable findings from source text, with LLM support and heuristic fallback.
- `NeoAPI.store_node()` embeds, stores, deduplicates by title/type when requested, auto-links related nodes, and can generate sparks.
- `LLMRelationshipJudge` can classify graph edges with a model.
- `SparkResolver` already gathers graph context, generates web queries, researches evidence, proposes candidate resolutions, judges them, and applies graph actions.

## Desired Architecture

### Ownership

Hermes agent owns research intent and judgment:

- choose what to research;
- decide whether a user-provided source is worth ingesting;
- decide which sparks matter;
- make the final intellectual call for important spark resolutions;
- describe learned knowledge as its own memory.

Neo owns durable knowledge engineering:

- source provenance;
- extraction of candidate durable findings;
- embedding and indexing;
- recall cues;
- dedupe;
- typed relationships;
- graph storage;
- compact recall signals;
- activity summaries.

Cheap/internal workers may do grunt work, but should not be the final source of truth for important theories or answers.

### Scheduled Research

Target flow:

1. Hermes cron runs a scheduled research prompt for the agent.
2. The agent reviews its specialty, recent work, unresolved sparks, stale domains, and configured source preferences.
3. The agent chooses research targets and sources.
4. Cheap workers fetch pages/transcripts, summarize, extract candidate claims, and collect evidence.
5. The agent or configured high-quality reasoning model approves the synthesis and graph action.
6. Neo stores durable findings, relationships, provenance, recall cues, and follow-up sparks.
7. The agent can later say it has learned or remembered the topic without flooding context.

### On-Demand Source Ingestion

Target flow when the user gives a link:

1. User asks the agent to research/remember a URL.
2. Hermes skill/tool fetches the source or delegates fetching to Neo.
3. Agent reviews relevance and intent.
4. Neo ingestion extracts candidate durable findings and provenance.
5. Agent approves or asks Neo to store.
6. Agent reports naturally: "I added the useful parts to my research memory."

### Spark Resolution

Spark resolution should be agent-led:

- Cheap model: gather search evidence, summarize sources, draft candidate interpretations.
- Agent/reasoning model: decide what is actually believed, what remains uncertain, and what should be stored.
- Neo: stores resolution nodes, typed relationships, provenance, and remaining sparks.

## Context Bloat Controls

Automatic recall must stay tiny:

- Most turns: inject nothing.
- Weak match: inject nothing.
- Medium match: inject 1-3 signal bullets.
- Strong match: optional micro-summary.
- Full source/graph content: only after explicit `neo_search`, `neo_get_node`, MCP, or research-memory tool retrieval.

The agent should receive a cue like "I have relevant research memory about this," then deliberately retrieve if needed.

## Implementation Phases

### Phase 1: Model Configuration Foundation

Add task-specific model settings for:

- research planning and source selection;
- ingestion/extraction;
- recall/reranking;
- existing resolution and relationship classification.

Add first-class Gemini support to Neo's provider-normalized LLM client while preserving local model support through Ollama, LM Studio, vLLM, llama.cpp, and OpenAI-compatible endpoints.

Initial config shape:

```env
NEO_LLM_RESEARCH_PROVIDER=gemini
NEO_LLM_RESEARCH_MODEL=gemini-2.5-flash
NEO_LLM_RESEARCH_API_KEY=...

NEO_LLM_INGESTION_PROVIDER=ollama
NEO_LLM_INGESTION_MODEL=qwen2.5:7b

NEO_LLM_RECALL_PROVIDER=lmstudio
NEO_LLM_RECALL_MODEL=local-reranker
NEO_LLM_RECALL_BASE_URL=http://127.0.0.1:1234/v1

NEO_LLM_RESOLUTION_PROVIDER=gemini
NEO_LLM_RESOLUTION_MODEL=gemini-2.5-pro
NEO_LLM_RESOLUTION_API_KEY=...
```

Implementation progress:

- `research`, `ingestion`, `recall`, and `rerank` task config slots added.
- Gemini support added to Neo's internal LLM client.
- Discovery now uses the research model for query generation and the ingestion model for source extraction instead of borrowing the resolution model.

### Phase 2: Agent-Facing Ingestion Entry Points

Add explicit ingestion APIs/tools for source URLs and source text:

- ingest URL/source on demand;
- return a preview of candidate findings before write when requested;
- write approved findings with provenance, relationships, and recall cues.

Hermes can wrap this in a skill such as "research and remember this link."

Implementation progress:

- Added `NeoAPI.ingest_source_text()` and `NeoAPI.ingest_source_url()`.
- Added MCP tool `ingest_source_url`.
- Added Hermes provider tool `neo_ingest_url`.
- Ingestion now stores source records for generic URLs, source provenance, and `recall_cues` metadata.
- Node embeddings include recall cues when present, without adding those cues to user-visible node content.
- Added Hermes skill `research/semantic-memory-research` in the local Hermes checkout for on-demand link ingestion and cron prompt guidance.

### Phase 3: Recall Quality

Improve the semantic radar:

- add `recall_cues` metadata during ingestion;
- index or include cues in embeddings/search text;
- expand candidates through graph neighborhoods;
- add a cheap reranker with threshold bands: none, hint, signal, micro-summary, retrieve-now;
- add an evaluation set for exact, adjacent, analogical, false-positive, and no-match cases.

### Phase 4: Agent-Led Scheduled Research

Move scheduling intent toward Hermes:

- Hermes cron prompt asks the agent what to research next;
- agent can call Neo tools for sparks/activity/gaps;
- agent chooses sources;
- Neo handles ingestion and storage;
- scheduled runs produce compact first-person research updates.

### Phase 5: Agent-Led Spark Resolution

Expose spark-resolution workflow so the agent can select and resolve sparks deliberately:

- list high-value sparks;
- gather evidence cheaply;
- present candidate resolution actions;
- let the agent/reasoning model make the final decision;
- store the result in Neo with provenance and remaining uncertainty.
