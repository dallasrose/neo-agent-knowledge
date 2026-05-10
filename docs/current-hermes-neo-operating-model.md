# Current Hermes + Neo Operating Model

Last updated: 2026-05-03

## Architecture

Hermes is the thinking agent. Neo is the semantic memory and storage substrate.

Hermes owns:

- deciding what to research;
- searching the web and reading sources;
- judging whether a source matters;
- deciding which claims, syntheses, or spark resolutions are worth keeping;
- calling Neo tools to retrieve or store memory.

Neo owns:

- durable semantic storage;
- source provenance;
- ingestion/extraction from a source the agent provides;
- embeddings and recall cues;
- dedupe and graph storage;
- passive semantic recall signals for the current turn.

Neo should not autonomously choose research topics or run its own research agenda in this setup.

## Live Configuration Intent

Background Neo autonomy is disabled:

```env
NEO_DISCOVERY_ENABLED=false
NEO_CONTEMPLATION_ENABLED=false
NEO_RESOLUTION_ENABLED=false
NEO_CONSOLIDATION_ENABLED=false
```

The agent can still explicitly trigger Neo tools:

- `neo_search`
- `neo_get_node`
- `neo_ingest_url`
- `neo_remember`
- `neo_sparks`
- `neo_investigate_spark`
- `neo_resolve_spark`
- `neo_abandon_spark`

## Local Model Usage

LM Studio is used for ingestion/extraction grunt work:

```env
NEO_LLM_INGESTION_PROVIDER=lmstudio
NEO_LLM_INGESTION_MODEL=google/gemma-4-e2b
NEO_LLM_INGESTION_BASE_URL=http://127.0.0.1:1234/v1
```

Do not use Neo's `research` model lane for this operating model. Source discovery should happen in Hermes skills.

Keep higher-judgment lanes on stronger models or explicit agent review:

- relationship classification;
- spark resolution;
- consolidation/synthesis;
- final claims/theories.

## Passive Recall

Neo's passive recall is intentionally still internal to the memory provider.

Flow:

```text
Hermes user turn
-> MemoryManager.prefetch_all()
-> NeoMemoryProvider.prefetch()
-> embedding/vector search + graph expansion
-> deterministic signal gating
-> tiny memory-context signal injected into Hermes
```

This is not a subagent and does not call an LLM. It is cheap retrieval, and it prevents the agent from needing to remember to search semantic memory manually on every turn.

When a signal appears, the agent should treat it as: "I may have relevant prior research here." If the signal affects the answer, the agent should call `neo_search` or `neo_get_node` for details.

## Live Graph Shape

The intended top-level graph shape is:

```text
Agents
├── Atlas
└── Wave
```

There should be no `Default` agent, no `Default` node, no duplicate `Agents` roots, and no top-level `Neo Instructions` node.

## Boundary Rule

Neo stores and retrieves. Hermes thinks, researches, decides, and instructs Neo what to keep.
