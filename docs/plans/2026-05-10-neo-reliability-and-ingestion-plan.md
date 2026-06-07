# Neo Reliability, Long-Source Ingestion, Search, Env, and Spark Hygiene Implementation Plan

> **For Hermes:** Use `subagent-driven-development` skill to implement this plan task-by-task. Do not implement directly from memory. Verify live config and service state before touching production services.

**Created:** 2026-05-10 11:01 EDT  
**Workspace:** `/Users/atlasai`  
**Project:** `/Users/atlasai/Documents/Code/Neo`  
**Primary goal:** Make Neo behave like a reliable person reading/listening to source material: read everything, ignore fluff, preserve every important durable point, retain source provenance, and resolve sparks without turning the graph into a junk drawer.

**Architecture:** Move Neo from “best-effort LLM extraction with silent fallback” to a staged ingestion pipeline with explicit source acquisition, transcript cleanup, atomic finding extraction, quality filtering, provenance, and observable failure states. Keep production usage on the installed package, not source checkout leakage. Treat web search/extraction as a first-class dependency with provider-level configuration and smoke tests.

**Tech Stack:** Python, Neo source at `/Users/atlasai/Documents/Code/Neo`, `uv`, pytest, launchd, SQLite `~/.neo/neo.db`, Hermes Agent tools/docs, MiniMax/OpenRouter/OpenAI-compatible APIs, Tavily/Firecrawl/SearXNG/Exa web providers.

---

## Executive Decisions

### 1. Output length should not be the constraint

Correct. Hard caps are dumb here. The real limit should be semantic quality: “is this an important reusable point?” not “did we hit finding #8?”

Current code already moved in the right direction:

- `src/neo/core/discovery.py:53-56` has `_MAX_SOURCE_TEXT_CHARS`, `_EXTRACTION_CHUNK_CHARS`, `_DEFAULT_FINDINGS_PER_PASS`, and `_MAX_EXTRACTION_PASSES_PER_CHUNK`.
- `extract_knowledge_findings()` at `src/neo/core/discovery.py:394-533` chunks long sources and loops extraction passes until no more findings remain.
- `max_findings=None` means no source-level cap.

But the pipeline still needs better reliability and observability:

- No silent fallback mush.
- Better transcript cleanup before extraction.
- Better distinction between “summary,” “important point,” “quote,” “question,” “ad read,” and “banter.”
- Tests proving full transcripts get processed across all chunks.

### 2. Do not use “caveman speak” as the compression strategy

Funny idea. Bad default. It is lossy in exactly the way we do not want. Caveman compression destroys nuance, attribution, and conditions — which are the whole value in podcasts and essays.

Better version: **claim ledger compression**.

Instead of turning transcript into “man say brand good,” reduce each segment into structured atomic notes:

```json
{
  "claim": "Premium positioning requires intentional scarcity, not accidental unavailability.",
  "why_it_matters": "Rise should design exclusivity as part of the offer instead of apologizing for limited capacity.",
  "evidence_or_context": "Speaker contrasts scarcity as status signal vs operational bottleneck.",
  "applies_to": ["Rise Recordings", "studio positioning", "premium brand"],
  "reject_if": "The business needs broad access/volume instead of high-status selectivity.",
  "source_span": {"chunk": 3, "approx_start": "00:22:10", "approx_end": "00:24:00"},
  "confidence": 0.88
}
```

That strips fluff without flattening intelligence into oatmeal.

### 3. Cheap summary-before-processing is useful only as routing, not as final memory

Use a cheap/small model for:

- Transcript cleanup.
- Segment classification.
- “Skip this chunk, it is intro/ad/banter.”
- Topic map / rough index.

Do **not** use the cheap summary as the source of truth for memory. That creates summary-of-summary decay. The final important findings should be extracted from the cleaned original chunk text, not a vague pre-summary.

Recommended flow:

1. Acquire full source text/transcript.
2. Clean and segment.
3. Cheap model marks low-value spans and builds a topic index.
4. Main ingestion model extracts durable findings from each retained segment.
5. Deduper merges near-duplicates.
6. Store atomic findings with provenance and confidence.

### 4. MiniMax provider being set to `openai` is ugly but technically explainable

MiniMax is not OpenAI. Dallas is right.

The reason it is currently configured as `openai` in some Neo `.env` paths is that `NeoLLMClient` uses provider names as **wire protocol adapters**, not brand names:

- `src/neo/core/llm.py:8-18` marks `openai`, `openrouter`, `ollama`, etc. as OpenAI-compatible.
- `src/neo/core/llm.py:20-25` marks `minimax` as Anthropic-compatible.
- `src/neo/core/llm.py:137-163` sends OpenAI-compatible requests to `/chat/completions`.
- MiniMax M2.7 can be reached through an OpenAI-compatible `/v1/chat/completions` endpoint.

So setting `NEO_LLM_INGESTION_PROVIDER=openai` with `NEO_LLM_INGESTION_BASE_URL=https://api.minimax.io/v1` really means: “use the OpenAI-compatible transport against MiniMax’s endpoint.”

Still, the config name lies. Plan: add explicit aliases like `minimax-openai` / `minimax_chat_completions` or switch `minimax` normalization based on base URL/API mode. Humans should not have to decode adapter nonsense.

### 5. OpenRouter is probably not “out of budget”; diagnose request shape first

Do not yank OpenRouter purely because of one error. Dallas says the budget is available. The plan should test:

- Does OpenRouter small-call work with the configured API key?
- Did Neo request excessive `max_tokens` or wrong model slug?
- Did OpenRouter reject because of provider routing / model endpoint / context / output-token policy?
- Did Neo swallow the LLM error and store fallback findings anyway?

If OpenRouter passes smoke tests and MiniMax is slower/more expensive, use OpenRouter for cheap routing/cleanup and MiniMax M2.7 for high-value extraction/resolution. If OpenRouter is flaky for long transcript extraction, yank it from that path specifically.

### 6. REST is useful, but production should not depend on REST for core memory if direct API works

Neo REST currently matters for:

- Visualizer/dashboard.
- Scheduled background discovery/resolution startup in `src/neo/rest/app.py`.
- Manual API inspection/health checks.

Hermes memory integration and CLI workflows can use direct Python/provider paths. The plan should keep REST for visualizer and schedulers, but avoid making core ingestion depend on REST when direct API is more reliable.

### 7. Abandoned sparks should not clutter the visible/active system

Dallas’s instinct is right: abandoned sparks are operational trash unless retained for short audit/debug. They should not remain visible as “things.”

Plan:

- Keep `resolved` sparks for provenance.
- Convert `abandoned` from permanent status clutter to either:
  - hard delete immediately, or
  - soft-delete with TTL and excluded from all normal APIs/UI.
- For duplicates/orphans/low-quality sparks, deletion is fine.

### 8. “Resolve all sparks every run” needs guardrails, not an arbitrary cap of 3

The current `resolution_batch_size=3` exists because the resolver is slow and LLM-heavy. Observed resolution times are ~95–235 seconds per spark, and too much concurrency causes timeouts and “No text block” failures.

But Dallas is right that the system should not leave a giant queue forever. The fix is not “3 forever.” The fix is:

- Sequential or low-concurrency work queue.
- Time-boxed run duration, not tiny item count.
- Backpressure based on provider health.
- Retry policy.
- Auto-delete/merge duplicate sparks before resolution.
- Generate fewer junk sparks upstream.

---

## Current Facts Verified This Session

### Files inspected

- `/Users/atlasai/Documents/Code/Neo/src/neo/config.py`
- `/Users/atlasai/Documents/Code/Neo/src/neo/core/discovery.py`
- `/Users/atlasai/Documents/Code/Neo/src/neo/core/llm.py`
- `/Users/atlasai/Documents/Code/Neo/src/neo/core/web_search.py`
- `/Users/atlasai/Documents/Code/Neo/src/neo/core/resolution_scheduler.py`
- `/Users/atlasai/Documents/Code/Neo/src/neo/store/sqlite.py`
- `/Users/atlasai/Documents/Code/Neo/tests/test_source_ingestion.py`
- `/Users/atlasai/Documents/Code/Neo/tests/test_web_search.py`

### Hermes web search docs read

Docs: <https://hermes-agent.nousresearch.com/docs/user-guide/features/web-search>

Key takeaways:

- Hermes supports provider-level `web_search` and `web_extract`.
- Firecrawl is the default and supports search, extract, crawl.
- SearXNG is free/self-hostable but search-only.
- Tavily/Firecrawl/Parallel support search + extract/crawl.
- Search and extract providers can be split:

```yaml
web:
  search_backend: "searxng"
  extract_backend: "firecrawl"
```

This matters because Neo currently has its own `WebSearchClient`, separate from Hermes’s richer web provider stack.

### Git state warning

`/Users/atlasai/Documents/Code/Neo` is already dirty with many modified files and backup env files. Do not bulldoze. Before implementation, create a git worktree or commit/stash intentionally.

---

## Implementation Tasks

## Phase 0 — Safety, Baseline, and Branch Discipline

### Task 0.1: Snapshot current work before changing anything

**Objective:** Avoid turning the current dirty repo into archeological lasagna.

**Files:**
- Inspect only: `/Users/atlasai/Documents/Code/Neo`

**Steps:**

1. Run:

```bash
cd /Users/atlasai/Documents/Code/Neo
git status --short
git diff --stat
git branch --show-current
```

2. Save current diff to a patch:

```bash
mkdir -p /Users/atlasai/.neo/backups
DATE=$(date +%Y%m%d-%H%M%S)
git diff > "/Users/atlasai/.neo/backups/neo-pre-reliability-plan-$DATE.patch"
git status --short > "/Users/atlasai/.neo/backups/neo-pre-reliability-plan-$DATE.status.txt"
```

3. Decide whether to work in current branch or create worktree:

```bash
git worktree add /Users/atlasai/Documents/Code/Neo-reliability-plan -b feat/neo-reliable-ingestion-sparks
```

**Verification:**

```bash
test -s /Users/atlasai/.neo/backups/neo-pre-reliability-plan-*.patch
```

Expected: backup patch exists and is non-empty if repo had diffs.

---

### Task 0.2: Audit installed vs source package path

**Objective:** Prove whether production services import installed Neo or the source checkout.

**Files:**
- Inspect: LaunchAgents in `/Users/atlasai/Library/LaunchAgents/`
- Inspect: `/Users/atlasai/.neo/.env`
- Inspect: `/Users/atlasai/Documents/Code/Neo/.env`

**Steps:**

Run:

```bash
launchctl print gui/$(id -u) | grep -i neo -A5 -B5 || true
ps aux | grep -E "neo|serve-rest|serve --agent" | grep -v grep || true
python - <<'PY'
import neo, inspect
print(inspect.getfile(neo))
PY
/Users/atlasai/.local/bin/neo status || true
```

**Expected:** Production should import from installed package path, ideally uv tool path, not `/Users/atlasai/Documents/Code/Neo/src/neo` unless intentionally in dev mode.

**Acceptance:** Document actual paths in `docs/current-hermes-neo-operating-model.md` or a new ops note.

---

## Phase 1 — Canonical Env and Installed-Package Production

### Task 1.1: Make `~/.neo/.env` the only production env

**Objective:** Stop source checkout `.env` from shadowing production settings.

**Problem found:** `SettingsConfigDict` in `src/neo/config.py:16-19` loads both `~/.neo/.env` and `.env` from the current working directory:

```python
model_config = SettingsConfigDict(
    env_prefix="NEO_",
    env_file=(_DEFAULT_ENV, ".env"),
    env_file_encoding="utf-8",
    extra="ignore",
)
```

That is convenient for dev but dangerous in production when launchd uses `/Users/atlasai/Documents/Code/Neo` as `WorkingDirectory`.

**Files:**
- Modify: `src/neo/config.py`
- Add tests: `tests/test_config.py`

**Implementation approach:**

Add explicit dev opt-in for cwd `.env`, e.g. `NEO_LOAD_CWD_ENV=true`, or split config:

- Default/prod: load only `~/.neo/.env`.
- Dev/test: allow explicit `_env_file` or `NEO_ENV_FILE`.

Potential code shape:

```python
import os

_ENV_FILES = (_DEFAULT_ENV,)
if os.getenv("NEO_LOAD_CWD_ENV", "").lower() in {"1", "true", "yes"}:
    _ENV_FILES = (_DEFAULT_ENV, ".env")

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEO_",
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )
```

**Tests:**

- `Settings(_env_file=...)` still works for tests.
- CWD `.env` does not override `~/.neo/.env` unless opt-in enabled.
- `NEO_LOAD_CWD_ENV=true` retains dev convenience.

**Run:**

```bash
cd /Users/atlasai/Documents/Code/Neo
uv run pytest tests/test_config.py -q
```

---

### Task 1.2: Update launchd services to use installed Neo binary and neutral working directory

**Objective:** Dallas uses Neo like any user; source updates go through dev → test → install → prod test.

**Files:**
- Inspect/modify launch agents under `/Users/atlasai/Library/LaunchAgents/`

**Production rule:**

- `ProgramArguments` should call installed `neo` binary.
- `WorkingDirectory` should be `$HOME` or omitted, not `/Users/atlasai/Documents/Code/Neo`.
- Production env should come from `~/.neo/.env`.
- No `PYTHONPATH=/Users/atlasai/Documents/Code/Neo/src` in launchd.

**Verification:**

```bash
launchctl kickstart -k gui/$(id -u)/ai.neo.visualizer
sleep 3
curl -s http://127.0.0.1:8420/api/health | jq .
python - <<'PY'
import neo, inspect
print(inspect.getfile(neo))
PY
```

**Acceptance:** Health returns correct agent `atlas`; imported package path is installed package, not source checkout.

---

### Task 1.3: Formalize dev → test → install → prod workflow

**Objective:** Make the lifecycle boring. Boring is good. Boring means fewer gremlins.

**Files:**
- Add: `docs/neo-production-release-workflow.md`
- Possibly add: `scripts/install-production.sh`

**Workflow:**

1. Develop on feature branch/worktree.
2. Run focused tests.
3. Run full tests.
4. Build package.
5. Install via `uv tool install --force` from local path or wheel.
6. Restart services.
7. Run production smoke tests.

**Commands:**

```bash
cd /Users/atlasai/Documents/Code/Neo
uv run pytest -q
uv build
uv tool install --force dist/*.whl
launchctl kickstart -k gui/$(id -u)/ai.neo.visualizer
```

**Acceptance:** A future update can be done without guessing which code production is running.

---

## Phase 2 — LLM Provider Configuration and Diagnostics

### Task 2.1: Rename provider configuration concepts so `openai` does not mean “MiniMax”

**Objective:** Fix the semantic lie.

**Files:**
- Modify: `src/neo/core/llm.py`
- Modify: `src/neo/config.py` if needed
- Add tests: `tests/test_llm_client.py`, `tests/test_config.py`

**Current issue:**

`minimax` normalizes to Anthropic adapter:

```python
_ANTHROPIC_COMPATIBLE = {
    "anthropic",
    "anthropic-compatible",
    "anthropic_compatible",
    "minimax",
}
```

But MiniMax M2.7 at `https://api.minimax.io/v1` uses OpenAI-compatible `/chat/completions`.

**Fix options:**

Preferred:

```python
_OPENAI_COMPATIBLE = {
    "openai",
    "openai-compatible",
    "openai_compatible",
    "openrouter",
    "minimax-openai",
    "minimax_chat_completions",
    ...
}

_ANTHROPIC_COMPATIBLE = {
    "anthropic",
    "anthropic-compatible",
    "anthropic_compatible",
    "minimax-anthropic",
}
```

Backward-compatible behavior:

- Keep `provider=openai` working.
- Add `provider=minimax-openai` as the recommended config.
- Do not make plain `minimax` ambiguous unless API mode is added.

**Tests:**

- `minimax-openai` resolves to OpenAI-compatible adapter and appends `/chat/completions`.
- `minimax-anthropic` resolves to Anthropic-compatible adapter.
- `openrouter` still uses `https://openrouter.ai/api/v1` by default.

---

### Task 2.2: Add provider smoke test CLI

**Objective:** Before ingestion jobs run for 20 minutes and store garbage, prove the LLM route works.

**Files:**
- Modify: `src/neo/cli/main.py`
- Add tests: `tests/test_cli.py` or existing CLI test file

**New command:**

```bash
neo doctor llm --task ingestion
neo doctor llm --task resolution
neo doctor search
neo doctor extract
```

**Behavior:**

- Print provider, model, base URL, configured API key presence only, never secret value.
- Send tiny JSON test prompt.
- Detect HTTP status and provider errors.
- Warn if request would use fallback.

**Acceptance example:**

```text
Task: ingestion
Provider: minimax-openai
Model: MiniMax-M2.7
Base URL: https://api.minimax.io/v1
API key: present
Smoke test: PASS
```

---

### Task 2.3: Diagnose OpenRouter specifically before yanking it

**Objective:** Separate “budget exhausted” from request-shape/model/provider failure.

**Files:**
- New script: `scripts/smoke_openrouter_ingestion.py`
- Or implement through `neo doctor llm --task ingestion`

**Checks:**

1. Small call with current configured OpenRouter model.
2. Medium call with expected ingestion `max_tokens`.
3. Long context call with synthetic transcript chunk.
4. Verify exact model slug.
5. Log HTTP status and response body excerpt.

**Acceptance:** Decision table:

| Result | Action |
|---|---|
| OpenRouter small + medium pass | Keep OpenRouter for cheap routing/cleanup. |
| OpenRouter small passes, long fails | Do not use OpenRouter for full extraction. Use MiniMax for extraction. |
| OpenRouter returns model routing/context errors | Fix model slug/request params. |
| OpenRouter returns actual billing/credit error | Yank from production tasks. |

---

## Phase 3 — Long-Source Ingestion: “Read Everything, Remember What Matters”

### Task 3.1: Add source acquisition metadata and failure visibility

**Objective:** No more silent “ok=true but fallback mush.”

**Files:**
- Modify: `src/neo/core/discovery.py`
- Modify store metadata paths in `src/neo/core/api.py` if needed
- Add tests: `tests/test_source_ingestion.py`

**Add metadata to every ingested source/finding:**

```json
{
  "source_url": "...",
  "source_title": "...",
  "source_type": "youtube|rss|url|manual",
  "manual_submission": true,
  "user_endorsed": true,
  "source_confidence_override": 0.95,
  "extraction_model": "MiniMax-M2.7",
  "extraction_provider": "minimax-openai",
  "fallback_used": false,
  "llm_error": null,
  "chunk_index": 3,
  "chunks_total": 12,
  "extraction_pass": 2
}
```

**Rule:** If LLM extraction fails, surface it:

- `preview` returns warning.
- production ingestion marks `fallback_used=true`.
- optionally refuse to store fallback findings unless `allow_fallback_store=true`.

**Acceptance:** Tests assert fallback is visible in metadata and not silently treated as high-confidence LLM extraction.

---

### Task 3.2: Add transcript/source cleanup stage

**Objective:** Strip fluff before extraction without losing actual ideas.

**Files:**
- Modify: `src/neo/core/discovery.py`
- Add tests: `tests/test_source_ingestion.py`

**New function:**

```python
def clean_source_segments(source_text: str, source_type: str) -> list[SourceSegment]:
    ...
```

**Segments should classify:**

- `content`
- `ad_read`
- `intro_outro`
- `banter`
- `question_only`
- `repeated_context`
- `transcript_artifact`

**Model or heuristic?**

Start heuristic + tests. Add optional cheap LLM classifier later.

**Acceptance cases:**

- Sponsor reads excluded.
- “Like and subscribe” excluded.
- Interview questions retained only when they frame a durable answer.
- A Seth Godin-style paragraph about strategy is retained even if conversational.

---

### Task 3.3: Replace “findings” with structured atomic extraction schema internally

**Objective:** Store useful ideas, not vague summaries.

**Files:**
- Modify: `src/neo/core/discovery.py`
- Modify formatter/tests if returned fields change

**Extraction JSON:**

```json
[
  {
    "title": "Premium positioning needs intentional scarcity",
    "claim": "Premium positioning is strengthened by deliberate scarcity when scarcity signals taste and selectivity rather than operational dysfunction.",
    "why_it_matters": "Rise can charge more and attract better-fit artists by making access feel curated, not merely limited.",
    "evidence_or_context": "Source argues that status comes from meaningfully constrained access.",
    "applies_to": ["Rise Recordings", "studio business", "brand positioning"],
    "reject_if": "The business goal is high-volume commodity throughput.",
    "summary": "Intentional scarcity can make premium positioning credible.",
    "content": "2-4 sentence durable memory text for recall.",
    "recall_cues": ["premium scarcity", "Rise positioning", "curated access"],
    "confidence": 0.9
  }
]
```

**Backward compatibility:** Map `claim/why_it_matters/evidence/reject_if` into existing `content` and metadata. Do not require DB migration immediately unless valuable.

---

### Task 3.4: Keep no artificial finding cap but add quality stop conditions

**Objective:** Unlimited important points, bounded runaway behavior.

**Current:** `_MAX_EXTRACTION_PASSES_PER_CHUNK = 25` is a guard.

**Improve stop condition:**

Stop when any of these occurs:

- Model returns `[]`.
- New findings are near-duplicates.
- Two consecutive passes add fewer than N new durable claims.
- Guard hit logs warning and marks metadata.

**Acceptance:** Dense chunk can produce >8 findings across multiple passes. Sparse chunk stops quickly.

---

### Task 3.5: Add map-reduce source synthesis after atomic extraction

**Objective:** For long podcasts, store both atomic findings and a useful source-level brief.

**Files:**
- Modify: `src/neo/core/discovery.py`
- Add tests: `tests/test_source_ingestion.py`

**Output:**

- Atomic nodes: each durable claim.
- Source synthesis node: “What this source teaches Dallas/Atlas.”
- Optional domain application: “How this applies to Rise / Montage / personal productivity.”

**Do not** let source summary replace atomic findings.

---

### Task 3.6: Manual submissions can carry high confidence and user intent

**Objective:** The Seth Godin link Dallas manually submitted and loved should shape Rise strategy at high confidence.

**Files:**
- Modify: Hermes Neo provider tool schema in `src/neo/integrations/hermes/provider.py`
- Modify ingestion API path in `src/neo/core/api.py` / `src/neo/core/discovery.py`
- Add tests: `tests/integrations/hermes/test_provider.py`, `tests/test_source_ingestion.py`

**Tool/API input additions:**

```json
{
  "url": "...",
  "title": "...",
  "domain": "rise-recordings",
  "query_focus": "brand positioning for Rise Recordings",
  "user_endorsed": true,
  "confidence": 0.95,
  "importance": "high",
  "notes": "Dallas loved this and wants Rise shaped around it."
}
```

**Storage rule:**

- Manual + user_endorsed source gets higher source confidence.
- Individual claims still can be lower if speculative, but base confidence is elevated.
- Recall cues should include explicit business context like `Rise Recordings`, `premium studio`, `Jamaica Plain`, etc. when provided.

---

## Phase 4 — Web Search and Extraction Reliability

### Task 4.1: Stop Neo from being trapped in its own weaker search client

**Objective:** Align Neo search/extraction with Hermes provider docs where useful.

**Current Neo:** `src/neo/core/web_search.py` supports Tavily, Exa, DuckDuckGo Lite. No Firecrawl, no SearXNG, no extraction/crawl abstraction.

**Hermes docs say:** Firecrawl default supports search/extract/crawl; SearXNG is free search-only; Tavily/Firecrawl/Parallel can extract.

**Plan:** Add a Neo web provider abstraction:

```python
class WebResearchClient:
    async def search(...): ...
    async def extract(urls: list[str], crawl: bool = False): ...
```

Providers:

- `tavily`
- `exa`
- `firecrawl`
- `searxng` search-only
- `duckduckgo` fallback search-only
- optional `hermes` adapter later if practical

**Files:**
- Modify: `src/neo/core/web_search.py`
- Add tests: `tests/test_web_search.py`

---

### Task 4.2: Fix/replace DuckDuckGo dependency

**Objective:** DuckDuckGo should be fallback only, not production foundation.

**Observed:** `WebSearchClient.multi_search()` with DuckDuckGo can return `[]` instantly. The current parser hits `https://lite.duckduckgo.com/lite/` and scrapes HTML. Brittle as hell, because scraping public search HTML always is.

**Decision:**

- Production search: Tavily or Firecrawl.
- Free/self-host production search: SearXNG with JSON enabled.
- DuckDuckGo: last-ditch fallback, with explicit warning when empty.

**Add tests:**

- DuckDuckGo empty page logs warning.
- SearXNG JSON parser works.
- Firecrawl/Tavily smoke tests skip without env.

---

### Task 4.3: Add extraction backend for normal URLs

**Objective:** Seth Godin/blog/manual URLs should be extracted reliably, not just discovered.

**Options:**

- Firecrawl for extract/crawl.
- Tavily extract if available.
- `trafilatura`/`readability-lxml` local fallback.

**Acceptance:** Given a blog URL, Neo stores the article body, not only title/description.

---

## Phase 5 — Spark Queue Hygiene and Resolution Throughput

### Task 5.1: Delete or TTL abandoned sparks

**Objective:** Abandoned sparks should not clutter the system.

**Files:**
- Modify: `src/neo/store/interface.py`
- Modify: `src/neo/store/sqlite.py`
- Modify: REST/MCP/provider surfaces if they expose abandoned sparks
- Add tests: `tests/test_store_interface.py`, `tests/test_sparks.py`

**Add store method:**

```python
async def delete_spark(self, spark_id: str) -> None:
    ...
```

**Policy:**

- Orphan duplicate junk: hard delete.
- Investigation abandoned for epistemic reason: either hard delete or TTL 7 days.
- Default UI/API: exclude abandoned unless `include_abandoned=true`.

**Migration not required** if deleting rows directly is safe.

---

### Task 5.2: Deduplicate sparks before resolution

**Objective:** Do not spend LLM calls resolving the same question 11 times. This is where money goes to die.

**Files:**
- Modify: `src/neo/core/resolution_scheduler.py`
- Possibly add helper in `src/neo/core/sparks.py`
- Add tests: `tests/test_spark_resolution_pipeline.py` or `tests/test_sparks.py`

**Algorithm:**

- Load active sparks up to a large candidate limit.
- Normalize description: lowercase, strip punctuation, collapse whitespace.
- Group by exact normalized text and/or target node + high similarity.
- Keep highest priority/newest canonical spark.
- Delete or mark duplicates before resolution.

**Acceptance:** Given 10 duplicate sparks, only 1 goes to resolver; 9 deleted/abandoned with reason.

---

### Task 5.3: Change resolution from item-count batch to time-boxed queue

**Objective:** Resolve more than 3 when healthy, without stampeding the provider.

**Current code:** `src/neo/core/resolution_scheduler.py:17` defaults `batch_size=3`, and `_tick()` processes sequentially.

**New config:**

```python
resolution_max_runtime_minutes: int = 45
resolution_max_concurrency: int = 1
resolution_batch_size: int = 25  # candidate ceiling, not final throughput
resolution_min_priority: float = 0.5
```

**Behavior:**

- Get up to N candidates.
- Deduplicate.
- Process sequentially or max concurrency 1-3.
- Stop when time budget is exhausted.
- Track successes/failures.
- Retry transient failures later.

**Acceptance:** A run can resolve 8-15 sparks if each is fast, or 2-3 if each takes 235s. The system adapts to reality instead of worshiping the number 3 like a tiny bureaucrat.

---

### Task 5.4: Generate fewer low-value sparks upstream

**Objective:** Queue cleanup is defensive. Better spark generation is the real fix.

**Files:**
- Modify: `src/neo/core/contemplation.py`
- Modify: `src/neo/core/sparks.py`
- Add tests around duplicate spark generation

**Rules:**

- Do not generate sparks that are just source comprehension questions already answered by parent content.
- Do not generate more than one spark per same node/question pattern in a contemplation pass.
- Prefer high-leverage unknowns tied to Dallas’s domains.

**Acceptance:** Contemplation pass should produce fewer, sharper sparks.

---

## Phase 6 — REST, Hermes, and Installed User Experience

### Task 6.1: Define why Neo REST exists

**Objective:** Remove architecture ambiguity.

**Doc update:** `docs/current-hermes-neo-operating-model.md`

**REST responsibilities:**

- Visualizer graph API.
- Health/status endpoint.
- Optional background scheduler host.
- Manual/admin endpoints.

**Non-REST responsibilities:**

- Hermes memory provider can use direct integration.
- Ingestion/resolution CLI can use direct Python API.
- Tests should not require REST unless testing REST.

---

### Task 6.2: Make Hermes integration report ingestion quality back to Dallas

**Objective:** When Dallas submits a link, he should know whether Neo actually learned from it.

**Files:**
- Modify: `src/neo/integrations/hermes/provider.py`
- Modify formatter in `src/neo/integrations/hermes/formatter.py`
- Tests in `tests/integrations/hermes/`

**Response should include:**

```text
Ingested: Seth Godin — [title]
Findings stored: 14
Chunks processed: 8/8
Model: MiniMax-M2.7 via minimax-openai
Fallback used: no
Confidence: high/user-endorsed
Top findings:
1. ...
2. ...
3. ...
```

If fallback occurred:

```text
Warning: LLM extraction failed; stored fallback findings only. This needs retry.
```

---

## Phase 7 — Tests and Verification Matrix

### Task 7.1: Focused unit tests

Run:

```bash
cd /Users/atlasai/Documents/Code/Neo
uv run pytest tests/test_config.py tests/test_llm_client.py tests/test_source_ingestion.py tests/test_web_search.py tests/test_store_interface.py -q
```

Expected: all pass.

---

### Task 7.2: Integration smoke tests

Run:

```bash
neo doctor llm --task ingestion
neo doctor llm --task resolution
neo doctor search
neo doctor extract
```

Expected: all pass or explicitly report missing provider config.

---

### Task 7.3: Manual Seth Godin link re-ingestion test

**Objective:** Prove Dallas’s manually loved source gets high-confidence treatment.

Run with the exact Seth Godin URL Dallas submitted.

Expected:

- No fallback unless reported.
- High-confidence/manual metadata present.
- Multiple durable business/brand findings stored.
- Findings recall under `Rise Recordings`, premium positioning, brand strategy queries.

---

### Task 7.4: Long podcast transcript test

**Objective:** Prove output length is not the constraint.

Use synthetic or real long transcript:

- Includes ad reads.
- Includes banter.
- Includes 20+ durable ideas across early/middle/late sections.

Expected:

- All chunks processed.
- Ads/banter skipped.
- >8 findings allowed.
- No global cap unless explicitly passed.
- Source synthesis generated.

---

### Task 7.5: Spark cleanup test

Seed test DB with:

- 20 duplicate active sparks.
- 5 orphan sparks.
- 10 legit unique sparks.

Expected:

- Duplicates deleted/merged.
- Orphans deleted/abandoned then TTL-purged.
- Legit sparks processed within time/concurrency budget.
- Active queue decreases.

---

## Proposed Production Config Targets

### Conservative/reliable config

```env
# ~/.neo/.env
NEO_AGENT_NAME=atlas

# Ingestion: high-quality, long-context-capable extraction
NEO_LLM_INGESTION_PROVIDER=minimax-openai
NEO_LLM_INGESTION_MODEL=MiniMax-M2.7
NEO_LLM_INGESTION_BASE_URL=https://api.minimax.io/v1
NEO_LLM_INGESTION_API_KEY=...

# Research/query planning: can be cheaper if smoke-tested
NEO_LLM_RESEARCH_PROVIDER=openrouter
NEO_LLM_RESEARCH_MODEL=google/gemini-2.5-flash-lite
NEO_LLM_RESEARCH_BASE_URL=https://openrouter.ai/api/v1
NEO_LLM_RESEARCH_API_KEY=...

# Resolution: quality matters; use MiniMax unless OpenRouter proves stable
NEO_LLM_RESOLUTION_PROVIDER=minimax-openai
NEO_LLM_RESOLUTION_MODEL=MiniMax-M2.7
NEO_LLM_RESOLUTION_BASE_URL=https://api.minimax.io/v1
NEO_LLM_RESOLUTION_API_KEY=...

# Search/extraction
NEO_SEARCH_PROVIDER=tavily
NEO_SEARCH_API_KEY=...

# Spark resolution
NEO_RESOLUTION_ENABLED=true
NEO_RESOLUTION_INTERVAL_MINUTES=30
NEO_RESOLUTION_BATCH_SIZE=25
NEO_RESOLUTION_MAX_RUNTIME_MINUTES=45
NEO_RESOLUTION_MAX_CONCURRENCY=1
```

### Lower-cost option

```env
# Cheap routing/cleanup/search-query generation
NEO_LLM_RESEARCH_PROVIDER=openrouter
NEO_LLM_RESEARCH_MODEL=google/gemini-2.5-flash-lite

# Main extraction still MiniMax unless OpenRouter long-source tests pass
NEO_LLM_INGESTION_PROVIDER=minimax-openai
NEO_LLM_INGESTION_MODEL=MiniMax-M2.7
```

### Web provider option from Hermes docs

If we want free search:

```yaml
web:
  search_backend: "searxng"
  extract_backend: "firecrawl"
```

For Neo native config, equivalent should be added:

```env
NEO_SEARCH_PROVIDER=searxng
NEO_SEARCH_BASE_URL=http://localhost:8888
NEO_EXTRACT_PROVIDER=firecrawl
NEO_EXTRACT_API_KEY=...
```

---

## Open Questions to Resolve During Implementation

1. Should `abandoned` sparks be hard-deleted immediately or retained for 7 days hidden from UI?
   - Recommendation: hard-delete duplicates/orphans; TTL-retain epistemic abandoned for 7 days max.

2. Should manual user-endorsed source confidence be global to all findings or just a base prior?
   - Recommendation: base prior. Individual finding confidence can still be lower if speculative.

3. Should source synthesis nodes be stored as `synthesis` node type or source metadata?
   - Recommendation: store as `synthesis` if it adds reusable knowledge; otherwise metadata brief.

4. Should Neo call Hermes `web_extract` directly?
   - Recommendation: not initially. Implement provider parity in Neo first. Direct Hermes tool coupling adds runtime weirdness.

5. Should OpenRouter remain in production?
   - Recommendation: yes for cheap tasks if smoke tests pass; no for high-value extraction if long-source reliability fails.

---

## Implementation Order

1. Phase 0 — snapshot and path audit.
2. Phase 1 — canonical env + installed package production.
3. Phase 2 — provider naming + smoke diagnostics.
4. Phase 3 — ingestion reliability and long-source extraction.
5. Phase 4 — search/extraction provider upgrades.
6. Phase 5 — spark cleanup and time-boxed resolution.
7. Phase 6 — Hermes/REST UX cleanup.
8. Phase 7 — full verification and production install.

---

## Definition of Done

This work is done when:

- A long podcast can be ingested without artificial finding caps.
- Ads, intros, banter, and low-value questions are stripped before memory storage.
- Important points are stored as atomic, source-grounded findings with provenance.
- Manual Dallas-endorsed sources can be marked high confidence and business-relevant.
- LLM failures are visible and do not silently produce fake “success.”
- OpenRouter vs MiniMax choice is based on smoke tests, not vibes.
- MiniMax config no longer has to pretend it is OpenAI.
- Production Neo uses the installed package and `~/.neo/.env`, not source checkout shadow config.
- Abandoned/duplicate/orphan sparks no longer clutter the graph.
- Spark resolution drains by time budget/provider health, not an arbitrary 3-item drip.
- REST’s role is documented and not accidentally required for direct memory paths.

---

## Dallas Translation

The move is not “summarize harder.” The move is **build a memory refinery**:

Raw transcript in → trash stripped → important claims extracted → duplicates merged → source/provenance attached → business relevance tagged → sparks resolved/cleaned → production runs from installed software like a normal user.

No more “it kinda ingested something.” Either Neo learned it, or it tells us exactly where it choked.
