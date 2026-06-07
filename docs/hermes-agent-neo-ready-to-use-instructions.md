# Hermes Agent Neo Ready-To-Use Instructions

Goal: get Atlas's Neo semantic memory into a clean, usable state using Neo's own functionality. Do not invent workarounds, do not directly mutate SQLite, and do not write one-off migration scripts unless Dallas explicitly asks.

## Core Principle

Neo is my semantic memory backend. Treat it as "my knowledge" in conversation, but be technically clear that Neo is the tool storing and retrieving it.

Your job is not to redesign Neo. Your job is to make the current memory usable and then proceed through normal Neo workflows.

## Hard Guardrails

Do not:

- Write ad hoc SQLite scripts.
- Add columns or mutate the DB directly.
- Create workaround migration scripts.
- Re-enable Neo MCP unless Dallas asks.
- Treat Neo as an agent/person in user-facing text.
- Bulk resolve or abandon sparks without reading them.
- Spend a lot of tokens polishing legacy data unless it directly improves readiness.

Do:

- Use Neo's official commands and Hermes Neo memory tools.
- Keep changes inspectable and reversible.
- Prefer small batches.
- Report blockers instead of hacking around missing product functionality.
- Use first-person language when talking as the agent: "I searched my memory," "I stored this in my knowledge."

## Current Intended Runtime Setup

Hermes should use Neo as a semantic memory provider, not as an MCP server.

Expected Hermes memory providers:

```yaml
memory:
  providers:
  - name: honcho
    type: episodic
  - name: neo
    type: semantic
    mode: signals-first
```

Expected Neo memory tools available to Hermes:

```text
neo_search
neo_get_node
neo_sparks
neo_investigate_spark
neo_resolve_spark
neo_abandon_spark
neo_ingest_url
neo_remember
```

If those tools are not available, stop and report that Hermes is not loading the Neo memory provider correctly.

## Model And Search Expectations

Neo should have internal LLM settings for:

```text
research
ingestion
resolution
relationship
```

Current acceptable setup:

- MiniMax or another configured cloud/local model for reasoning.
- DuckDuckGo search is acceptable as a free fallback while Tavily credits are unavailable.
- Gemini only works if a Gemini/Google API key is configured.

Do not assume Gemini works unless a key exists.

## Step 1: Verify Neo Is Pointing At The Real DB

The real Neo DB should be:

```text
/Users/atlasai/.neo/neo.db
```

The project source is:

```text
/Users/atlasai/Documents/Code/Neo
```

The installed package may live elsewhere, but Neo state should still point to `~/.neo`.

Verify with Neo/Python config, not by guessing.

Expected result:

- DB URI points to `/Users/atlasai/.neo/neo.db`.
- Hermes imports Neo from the intended install/source.
- No duplicate mystery DB is being used.

If there are multiple DBs, stop and report paths before doing anything.

## Step 2: Confirm Product Code Is Ready

Run focused tests from the Neo project folder:

```bash
cd /Users/atlasai/Documents/Code/Neo
.venv/bin/pytest \
  tests/test_web_search.py \
  tests/test_config.py \
  tests/test_llm_client.py \
  tests/test_relationships.py \
  tests/test_source_ingestion.py \
  tests/integrations/hermes/test_provider.py \
  tests/test_mcp_http.py::test_tools_list_returns_all_tools
```

Expected:

```text
all tests pass
```

If tests fail, stop and report the exact failing test and traceback. Do not patch randomly.

## Step 3: Verify Hermes Can Use Neo

From Hermes venv, verify Neo memory provider loads:

```bash
/Users/atlasai/.hermes/hermes-agent/.venv/bin/python - <<'PY'
from plugins.memory import load_memory_provider
p = load_memory_provider("neo")
print(type(p).__name__)
print([tool["name"] for tool in p.get_tool_schemas()])
PY
```

Expected tool list includes:

```text
neo_search
neo_get_node
neo_sparks
neo_investigate_spark
neo_resolve_spark
neo_abandon_spark
neo_ingest_url
neo_remember
```

If `neo_investigate_spark` is missing, Hermes is using stale Neo code.

## Step 4: Do Not Try To Migrate Data By Script

The old data may be imperfect because it predates the new architecture. That is okay.

Only clean data through these supported paths:

```text
neo_search
neo_get_node
neo_remember
neo_ingest_url
neo_sparks
neo_investigate_spark
neo_resolve_spark
neo_abandon_spark
neo relationships
```

If you believe the DB needs something Neo does not currently expose, report:

```text
Missing official Neo functionality:
- What needs to happen
- Why existing tools cannot do it
- What official command/tool should exist
```

Do not implement the workaround yourself.

## Step 5: Relationship Cleanup

Use Neo's official relationship command, not custom SQL.

From the Neo project folder:

```bash
cd /Users/atlasai/Documents/Code/Neo
.venv/bin/neo relationships --limit 100
```

Then inspect output.

If it succeeds:

- Note nodes processed.
- Note edges created.
- Note edges reclassified or skipped.

If it fails with LLM parsing:

- Report the error.
- Do not fall back to direct DB edits.
- Do not lower thresholds without approval.

Important: relationship quality matters more than edge count. Do not chase "2,000 edges" blindly. A sparse high-quality graph is better than a noisy dense graph.

## Step 6: Spark Queue Cleanup

Use small batches.

First list sparks:

```text
neo_sparks(limit=5)
```

For each spark, decide one of three paths:

1. Use `neo_investigate_spark`.
   - Best for real research questions, contradictions, or knowledge gaps.
   - Prefer `mode="preview"` first for expensive or ambiguous sparks.
   - Use `mode="apply"` when confident.

2. Use `neo_resolve_spark`.
   - Only when the answer is already stored or you have just stored a durable answer node.

3. Use `neo_abandon_spark`.
   - Only when the spark is a duplicate, false positive, too vague, or no longer useful.

Never inspect a spark and leave it half-handled if you made a determination.

Do not bulk abandon a domain just because it is common.

## Step 7: Search Quality Check

Run practical searches through Neo memory, not DB queries.

Use queries that should trigger associative recall:

```text
neo_search("studio lease personal guarantee exposure", top_k=5)
neo_search("agent architecture memory provider semantic recall", top_k=5)
neo_search("productivity psychology contradiction evidence", top_k=5)
neo_search("when should I remember research I already did", top_k=5)
```

For each query, evaluate:

```text
- Did relevant nodes appear?
- Were results concise enough for Hermes context?
- Did sparks appear when useful?
- Did results feel like semantic memory rather than keyword search?
```

If search quality is bad, report examples. Do not rewrite embeddings manually.

## Step 8: Ingestion Path Check

Test normal future ingestion with one safe source URL if Dallas provides one.

Use:

```text
neo_ingest_url(url="...", query_focus="...", max_findings=3, preview=true)
```

If preview looks good, run:

```text
neo_ingest_url(url="...", query_focus="...", max_findings=3, preview=false)
```

Then search for the ingested concept:

```text
neo_search("topic from ingested source", top_k=5)
```

Expected:

- Source is summarized into durable findings.
- Findings include provenance.
- Recall works naturally.
- No giant raw article dump is stored as memory.

## Step 9: Readiness Criteria

Neo is ready to use when all are true:

```text
[ ] Hermes loads Neo as a memory provider.
[ ] Neo MCP is disabled unless explicitly needed.
[ ] Neo memory tools include neo_investigate_spark.
[ ] Neo config has working internal LLM settings.
[ ] Neo search provider does not depend on unavailable Tavily credits.
[ ] Focused Neo tests pass.
[ ] Relationship judge works or failures are clearly reported.
[ ] A few real neo_search queries return useful results.
[ ] Spark workflow works through neo_sparks -> neo_investigate_spark / resolve / abandon.
[ ] Future ingestion works through neo_ingest_url or neo_remember.
```

## What To Report Back

Give Dallas a concise status report:

```text
Neo readiness status:
- Hermes provider: working / not working
- Neo tools available: ...
- Internal model config: ...
- Search provider: ...
- Tests: ...
- Relationship pass: ...
- Spark queue: ...
- Search quality examples: ...
- Blockers: ...
```

## Final Reminder

Do not optimize for making the old DB perfect. Optimize for making Neo safe and natural to use from this point forward.

The existing DB is seed knowledge. Clean it only where Neo's normal tools support doing so.
