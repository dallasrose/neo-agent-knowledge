# Hermes Agent Semantic Memory Handoff

Last updated: 2026-04-29

## What Changed

You now have two complementary memory systems:

- **Honcho**: episodic/user/conversation memory.
- **Neo**: semantic research memory: durable findings, concepts, theories, syntheses, contradictions, open questions, provenance, and relationships.

Do not frame Neo as a separate intelligence. Neo is the storage/retrieval substrate. The knowledge belongs to you, the agent.

Use natural first-person language with the user:

- "I remember researching this."
- "I have notes on this."
- "This connects to something I learned earlier."
- "I'll add the useful parts to my research memory."

Avoid:

- "Neo thinks..."
- "Neo learned..."
- "Neo updated its theory..."

It is fine to mention Neo when discussing tools, debugging, configuration, or storage mechanics.

## Current Hermes Setup

Hermes is configured to load multiple memory providers:

```yaml
memory:
  provider: honcho
  providers:
    - name: honcho
      type: episodic
    - name: neo
      type: semantic
      mode: signals-first
```

The Neo Hermes plugin is installed at:

```text
~/.hermes/plugins/neo/
```

Neo is installed into the Hermes venv in editable mode from:

```text
/Users/atlasai/Documents/Code/Neo
```

## Available Semantic Memory Tools

When Neo is active as a Hermes memory provider, you can use:

- `neo_search` — search semantic research memory.
- `neo_get_node` — inspect a specific memory node.
- `neo_sparks` — list active open questions/research sparks.
- `neo_resolve_spark` — mark a spark resolved after storing/identifying answer nodes.
- `neo_abandon_spark` — close a spark as not useful or not currently answerable.
- `neo_ingest_url` — read a URL and store durable findings in research memory.
- `neo_remember` — explicitly store durable semantic knowledge.

Use Neo for durable research/domain knowledge, not raw chat history.

## How To Use It In Conversation

When a Neo memory signal appears, treat it as an internal cue:

> "I may have relevant prior research here."

Then retrieve details before giving confident advice:

1. Call `neo_search` with the current topic/question.
2. Call `neo_get_node` if you need full details.
3. Synthesize the result in your own words.
4. Keep source/provenance/uncertainty visible when it materially affects the answer.

Do not dump raw graph data into the answer.

## How To Ingest A User-Provided Link

When the user says something like "research this", "remember this", "add this to your knowledge", or gives you a source link:

1. Infer the user's research intent.
2. Set a short `query_focus` if the source should be read through a specific lens.
3. Call `neo_ingest_url`.
4. Tell the user what durable findings were added.
5. If nothing durable was found, say so plainly.

Example intent:

```json
{
  "url": "https://example.com/article",
  "query_focus": "commercial lease risk for a recording studio",
  "domain": "studio-business"
}
```

Use `preview: true` only if the user wants to review before saving.

## Scheduled Research Job Prompt

Use this prompt for a Hermes cron job when you want to grow expertise over time:

```text
Review my current specialty, recent work, and unresolved research questions. Choose one high-value thing to learn next. Find a credible recent source or use my configured research sources. Ingest durable findings into my research memory, resolve or create sparks where appropriate, and report only a concise summary of what I learned, what changed in my understanding, and what remains uncertain.
```

Good schedules:

```text
0 8 * * *    daily research pass
0 */6 * * *  lightweight scan four times daily
```

The goal is not to spam context. The goal is to steadily build durable expertise.

## Spark Resolution Philosophy

You, the agent, should own spark resolution.

Cheap/internal models may help with grunt work:

- collecting sources;
- summarizing evidence;
- extracting candidate claims;
- drafting possible interpretations.

But do not let a cheap extraction step invent final theories or answers. For important sparks:

1. Call `neo_sparks`.
2. Choose a spark worth resolving.
3. Research evidence.
4. Store a durable answer/synthesis only when evidence supports it.
5. Call `neo_resolve_spark` with the answer node IDs.
6. If evidence is insufficient, leave uncertainty visible or preserve a follow-up spark.
7. Use `neo_abandon_spark` only when the spark is not useful or not currently answerable.

## Existing Knowledge Cleanup Needed

The existing Neo graph is valuable, but it predates the new semantic recall model.

Current observed state of `/Users/atlasai/.neo/neo.db`:

- 906 nodes
- 125 edges
- 2457 sparks
- 899 nodes with embeddings
- 0 nodes with `recall_cues`
- 0 nodes linked to normalized `neo_sources`
- 1369 active sparks

This means old knowledge is usable, but not yet optimized for natural associative recall.

## Cleanup Plan For Existing Knowledge

Do this as a safe migration/backfill, not by manually rewriting memory.

### 1. Backup First

Create a DB backup before any mutation:

```bash
cp ~/.neo/neo.db ~/.neo/neo.db.backup-before-recall-backfill-$(date +%Y%m%dT%H%M%S)
```

### 2. Backfill Recall Cues

For each non-system node, generate 2-6 short internal cue phrases describing when that memory should be recalled.

Examples:

```yaml
recall_cues:
  - commercial lease negotiation
  - studio relocation risk
  - tenant improvement allowance
  - personal guarantee exposure
```

Cues should be stored in node metadata, not user-visible content.

### 3. Re-Embed With Recall Cues

Recompute node embeddings using title + content + recall cues, so associative recall can match adjacent concepts.

This is what makes "studio lease" retrieve related knowledge about buildout risk, cash runway, personal guarantees, zoning, and acoustics even when the exact words differ.

### 4. Rebuild/Reclassify Relationships

The graph currently has many more nodes than edges. Run relationship building and, where configured, LLM relationship classification.

Prioritize useful typed edges:

- `supports`
- `contradicts`
- `extends`
- `example_of`
- `prerequisite_for`
- `questions`
- `resolves`
- `connects`

Avoid vague topical edges that do not help retrieval.

### 5. Spark Triage

There are too many active sparks for an agent-led research queue.

Cluster, rank, or prune sparks so the useful ones rise to the top:

- merge near-duplicates;
- abandon low-value sparks;
- prioritize sparks tied to core specialty domains;
- preserve high-value contradictions and unanswered questions.

### 6. Recover Provenance Where Possible

Old nodes are mostly not linked to `neo_sources`. Recover source URLs/titles from metadata or content when possible, but do not overfit or invent provenance.

If provenance is unclear, leave it unknown.

## Desired Migration Command

A good implementation target is a repeatable command like:

```bash
neo backfill-recall --agent-name atlas --backup --limit 200
```

or:

```bash
neo migrate-memory --recall-cues --reembed --relationships --spark-triage
```

It should support dry-run/preview mode before mutating the graph.

## Success Criteria

After cleanup:

- Most durable nodes have `recall_cues`.
- Embeddings include recall cue text.
- Relationship density improves without becoming noisy.
- Active sparks are fewer and more useful.
- The agent naturally recalls related research with tiny signals, not large context dumps.
- The user experiences this as the agent becoming a deeper expert over time.
