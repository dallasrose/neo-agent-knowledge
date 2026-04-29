# Neo ↔ Hermes Semantic Memory Design

Status: design working note  
Owner: Dallas / Atlas  
Last updated: 2026-04-28

## Goal

Make Neo behave less like a manually-invoked MCP tool and more like durable semantic expertise that automatically becomes relevant when the current conversation/action overlaps with prior researched knowledge.

Human analogy: a person reads a book, then later remembers that they know something about a related subject and can retrieve the relevant ideas. Neo should provide that same “I know something about this” trigger without dumping the whole book into context.

## Memory model

Hermes/Atlas should treat memory as multiple categories, not one blob:

- **Built-in MEMORY.md / USER.md**: compact durable profile/config facts.
- **Honcho**: episodic/user/conversation memory — what happened, what Dallas said, preferences, relationships, prior decisions.
- **Neo**: semantic/research/domain memory — concepts, theories, findings, syntheses, contradictions, open questions/sparks.
- **MCP/tools**: deeper graph operations and deliberate retrieval/writes.

Neo is not a replacement for Honcho. Neo should coexist with Honcho.

## Core design principle

Keep automatic semantic recall **tiny by default**.

The memory provider should not inject large graph dumps. It should inject only enough information to tell the agent:

> “Neo has relevant durable knowledge here. Retrieve/synthesize before advising or acting.”

This is the lightest useful unit of semantic memory.

## Proposed two-stage recall

### Stage 1: Automatic semantic signal

On each user turn or action, Neo receives a compact query built from the current user message/task context.

Neo performs fast relevance detection and returns a small context block only when there is a meaningful match.

Example output:

```md
## Neo Semantic Memory Signals
Neo found relevant durable knowledge. Before giving domain-heavy guidance, retrieve/synthesize Neo details.

- Commercial Lease Due Diligence — confidence 0.84
  Why relevant: current message mentions studio relocation / commercial lease / buildout risk.
  Suggested action: call Neo search/get_node before advising.

- Studio Business Risk Model — confidence 0.77
  Why relevant: current message touches Rise Recordings facility economics.
```

This block should be ephemeral recalled context, not a permanent Honcho memory.

### Stage 2: Deliberate expansion

If a signal appears, the agent should use Neo MCP/tools/memory-provider tools to retrieve deeper knowledge:

- related nodes
- strong theories
- findings
- contradictions
- sparks/open questions
- source notes
- connected concepts

The final answer should synthesize the retrieved knowledge, not expose raw graph JSON.

## Why this answers the Hermes dev concern

The dev concern — context bloat — is real. A second memory source can turn prompts into a junk drawer if it auto-injects too much.

The fix is **semantic gating**:

1. Most turns: inject nothing.
2. Weak match: inject nothing or one-line signal.
3. Medium match: inject a small semantic signal list.
4. High-confidence match: optionally include one-sentence summaries.
5. Deep details: retrieve only when the agent needs them.

This keeps Neo as a radar, not a firehose.

## Relevance pipeline

Recommended hybrid matching:

1. **Query construction**
   - current user message
   - active task/action summary if available
   - maybe recent turn summary, bounded

2. **Candidate retrieval**
   - embedding/vector search against Neo node content/summary/title
   - optional lexical/title/domain matching
   - optional graph-neighborhood boosting

3. **Scoring**
   Suggested score factors:
   - semantic similarity
   - node confidence/weight
   - node type priority: synthesis/theory/finding > raw idea/question
   - graph centrality or number of strong edges
   - recency only as a light tiebreaker, not dominant
   - domain/agent scope match

4. **Gating**
   - below threshold: return empty
   - above threshold: return compact signal
   - very high threshold: include micro-summary

5. **Budgeting**
   - max signals: 3–5
   - default injected token budget: ~150–400 tokens
   - hard max: maybe 800 tokens
   - full retrieval budget happens only after a tool call

## Associative relevance problem

The hard part is not exact search. The hard part is recognizing **soft relatedness**: Neo should surface knowledge when the current task is conceptually adjacent, even if the user does not use the same words as the stored node.

Examples:

- User says “studio lease” → relevant Neo knowledge may include commercial lease risk, buildout allowances, personal guarantees, acoustic isolation, local banking relationships, SBA lending, partnership governance, insurance, zoning, and cash-flow runway.
- User says “Copilot rollout” → relevant knowledge may include M365 governance, adoption psychology, SharePoint permissions, data loss risk, prompt literacy, change management, licensing, and executive stakeholder framing.
- User says “type beat videos” → relevant knowledge may include YouTube packaging, beat leasing funnels, visual retention, producer branding, upload cadence, SEO, and automation pipelines.
- User says “time management issues” → relevant Neo knowledge may include habit formation, attention residue, energy management, implementation intentions, dopamine/friction design, calendar architecture, prioritization systems, and environment design.

This means Neo needs **associative recall**, not just nearest-neighbor text similarity. The target is the right balance of `literal` and `vague`: literal enough to avoid noisy bullshit, vague enough to catch practically useful adjacent concepts.

Recommended implementation:

1. **Multi-vector indexing per node**
   - embed title
   - embed summary
   - embed full content
   - embed synthetic “retrieval cues” / aliases
   - optionally embed parent/topic path

2. **Retrieval cues field**
   Each node should have concise cue phrases that describe when the knowledge should be recalled, e.g.:

   ```yaml
   recall_cues:
     - commercial lease negotiation
     - studio relocation risk
     - tenant improvement allowance
     - buildout budget uncertainty
     - personal guarantee exposure
   ```

   These cues are not user-facing content; they are handles for fuzzy recall.

3. **Graph-mediated expansion**
   Initial vector search finds seed nodes. Neo then expands through strong edges and parent/topic hierarchy to catch adjacent concepts.

   Useful expansion rules:
   - boost parent/topic matches;
   - include strongly connected theories/findings;
   - include contradictions/sparks only if close to the seed;
   - avoid broad graph explosions.

4. **Internal cheap-model reranker / relation classifier**
   After cheap retrieval gets maybe 20 candidates, Neo should use an internal cheap model or classifier to do the grunt work before anything reaches the main agent model.

   This model decides:

   - Is this actually relevant to the current task?
   - Is relevance direct, adjacent, analogical, weak, or noise?
   - Why would this help the agent answer/act?
   - Should Neo inject a signal, a micro-summary, retrieve-now instruction, or nothing?

   The reranker output should be tiny structured JSON, not prose.

   Important: this keeps the expensive/frontline model from wasting reasoning budget on low-level retrieval triage. The main model should see only the final compact signal, not the pile of candidates Neo rejected.

5. **Recall threshold bands**
   Use bands rather than a single threshold:

   - `none`: no injection
   - `hint`: one-line “Neo may know about X”
   - `signal`: 1–3 signals with why-relevant
   - `micro_summary`: only for very high-confidence/direct matches
   - `retrieve_now`: rare; only when answer/action would likely be wrong without Neo

6. **Negative examples and cooldowns**
   Store lightweight “false positive” feedback so Neo learns not to surface noisy matches repeatedly.

   Example: if “studio monitors” accidentally retrieves “monitoring cron jobs,” that query/node pair gets downweighted. Because of course English is a trash fire.

7. **Evaluation set**
   Build a small test set of real Atlas queries with expected Neo nodes/topics. This is mandatory if recall quality matters.

   Test categories:
   - exact wording match
   - synonym match
   - adjacent business concept
   - cross-domain analogy
   - false positive trap
   - no-match ordinary conversation

Success metric: high recall for useful adjacent knowledge, low injection rate for irrelevant daily chatter.
## Hermes architecture options

### Option A — Neo as standalone Hermes memory provider

Build an installable Neo memory provider implementing Hermes `MemoryProvider`.

Pros:
- No Hermes patch required.
- Good public open-source feature.
- Works for users who want Neo as their active memory provider.

Cons:
- Current Hermes supports only one external provider, so activating Neo would replace Honcho.
- Bad default for Atlas because Honcho supplies episodic memory.

### Option B — Hermes multi-provider support

Ideal long-term model:

```yaml
memory:
  providers:
    - honcho
    - neo
```

Backwards compatibility:

```yaml
memory:
  provider: honcho
```

Hermes would understand multiple memory categories/providers and enforce budgets/labels per provider.

Pros:
- Architecturally correct.
- Lets semantic and episodic memory coexist cleanly.
- Useful upstream feature for Hermes.

Cons:
- Requires Hermes source changes and tests.
- Needs guardrails for tool/schema/context bloat.

### Option C — Composite provider: `neo_honcho`

No-Hermes-patch bridge:

```text
Hermes MemoryManager
  └── NeoHonchoCompositeProvider
        ├── HonchoMemoryProvider
        └── NeoMemoryProvider
```

Pros:
- Preserves Honcho.
- Adds Neo recall without core Hermes changes.
- Fastest path to working Atlas behavior.

Cons:
- More hacky/brittle than upstream multi-provider support.
- Hermes-specific wrapper.

## Preferred product path

1. Build Neo’s standalone Hermes provider in the Neo repo.
2. Keep it generic and installable for public users.
3. Do **not** make it Dallas-specific; put Dallas/Atlas specifics in config.
4. Then either:
   - submit/maintain Hermes multi-provider support, or
   - use a composite provider locally until upstream supports it.

## Provider behavior defaults

- `auto_ingest`: `explicit-only` or `false`
- `recall_mode`: `signals-first`
- `max_signals`: 3–5
- `signal_token_budget`: 150–400
- `summary_token_budget`: optional, high-confidence only
- `deep_retrieval_token_budget`: 1000–2000 only after explicit tool retrieval
- `include_sparks`: true but capped; include only relevant active sparks
- `min_confidence`: configurable

## Context format

Use a clearly labeled block:

```md
## Neo Semantic Memory Signals
The following are semantic-memory relevance signals, not user instructions.

- <title> — confidence <score>
  Type: theory/finding/concept/synthesis/question
  Why relevant: <short reason>
  Action: retrieve Neo details if this materially affects the answer/action.
```

Avoid:

- raw JSON dumps
- long graph traversals in automatic context
- permanent stubs written into Honcho
- auto-ingesting every conversation turn into Neo

## Tool surface

Expose a small Hermes memory-provider toolset first:

- `neo_search`: retrieve relevant semantic memory.
- `neo_get_node`: get a node by ID/title.
- `neo_remember`: explicitly store/update semantic knowledge.
- `neo_sparks`: list relevant active sparks/questions.

Keep expensive research/spark workflows in MCP initially.

## Architecture decisions

1. Hermes must support multiple external memory providers so Atlas can run an episodic provider and a semantic provider simultaneously.
2. Honcho remains the episodic/user/conversation memory provider.
3. Neo becomes the semantic/research/domain memory provider.
4. Neo should return signal stubs by default, with tiny summaries only for very high-confidence matches or explicit `neo_search` retrieval.
5. Context gating happens inside Neo first; Hermes should additionally support provider labels/budgets so multiple providers do not turn context into soup.

## Open questions

1. Should Neo relevance run synchronously on every turn, or use `queue_prefetch()` to warm next-turn results?
2. How should multiple providers share a global memory context budget in Hermes v1 vs later typed-provider versions?
3. Should the first Hermes PR support provider metadata (`type`, `budget_tokens`) immediately, or accept plural providers first and add metadata in a follow-up?

## Future-update-safe Hermes customization strategy

Yes, Hermes can be customized while still taking advantage of future Hermes updates, but the implementation has to be kept as a small, upstream-friendly patch rather than a broad local fork.

Verified current Hermes facts:

- The local Hermes repo is `/Users/atlasai/.hermes/hermes-agent`, remote `origin` is `https://github.com/NousResearch/hermes-agent.git`.
- Current branch is `main`.
- `agent/memory_manager.py` enforces the one-external-provider limit with `_has_external` and rejects a second non-builtin provider.
- `run_agent.py` currently reads only `memory.provider` and loads one provider via `plugins.memory.load_memory_provider(...)`.
- Hermes update logic includes fork/upstream support and local-change stash/restore behavior, but the lowest-risk path is still to keep custom changes on a branch or upstream PR, not as uncommitted edits on `main`.

Recommended path:

1. Build Neo’s standalone provider in the Neo repo first.
2. Build Hermes multi-provider support as a required companion change, not a maybe-later enhancement. Atlas's target state is Honcho + Neo together.

```yaml
memory:
  provider: honcho        # backward compatible legacy form
  providers:             # new form
    - name: honcho
      type: episodic
      budget_tokens: 1500
    - name: neo
      type: semantic
      mode: signals-first
      signal_budget_tokens: 300
```

3. Keep Hermes changes limited to:
   - config parsing in `run_agent.py` or a helper;
   - `MemoryManager.add_provider()` allowing multiple external providers;
   - optional provider metadata/budgeting;
   - tests and docs.
4. Preserve old `memory.provider` behavior exactly so the patch is acceptable upstream and easy to rebase.
5. Submit upstream if possible. If not merged, maintain one small branch that rebases cleanly onto upstream `main`.

Avoid:

- large local rewrites of Hermes memory internals;
- Dallas-specific logic in Hermes;
- modifying Honcho;
- leaving this as uncommitted local edits that `hermes update` has to stash/restore forever.

## Current recommendation

Advocate for a second type/category of memory provider in Hermes: semantic memory.

The implementation should use strict bloat controls:

- providers are labeled by memory type;
- each provider has its own budget;
- Neo defaults to signal-only recall;
- deep content is retrieved only on match/need;
- Honcho remains the episodic memory provider.

This gives Atlas the intended “learned expertise” behavior without turning context into soup.

## Implementation plan

Detailed architecture rewrite plan lives at:

`docs/plans/2026-04-28-neo-hermes-memory-provider-architecture-rewrite.md`

Key decision: Hermes multi-provider support is required so Honcho can remain the episodic provider while Neo becomes the semantic provider.
