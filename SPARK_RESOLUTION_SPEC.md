# Spark Resolution Spec

Temporary implementation spec for the v0.1 spark resolution upgrade. The
permanent behavior is summarized in `Neo_Spec_v0.4.md` and
`neo-architecture.md`.

## Goals

- One spark resolution process must run the same way whether triggered by the
  background scheduler or an MCP tool.
- Resolved sparks must disappear from the active queue and the visualizer.
- Nodes that absorb resolved sparks become visually stronger: resolved spark
  count increases node strength and blends node color toward gold.
- A spark is not always a challenge to the current graph. The graph provides
  context; spark-type-specific agents generate competing interpretations or
  resolution paths.
- Resolution must support "no durable graph change" as a valid outcome.

## Pipeline

1. Collect graph context for the spark, target node, related nodes, and existing
   knowledge search results.
2. Collect external evidence with generated search queries when web search is
   configured. If search is unavailable, continue with graph context only.
3. Generate Candidate A with a role-isolated position agent.
4. Generate Candidate B with a second role-isolated position agent.
5. Generate Candidate AB with a synthesis agent.
6. Run three blind judge agents over anonymized candidates.
7. Apply the winning decision only when mode is `apply`.

## Spark-Type Framing

- `contradiction`: A and B defend different claims or readings; AB reconciles,
  chooses, or preserves uncertainty.
- `open_question`: A and B answer from distinct evidence-backed perspectives;
  AB gives the best current answer and remaining uncertainty.
- `weak_edge`: A argues the connection is useful; B argues it is weak, indirect,
  or mis-typed; AB decides what graph relationship should remain.
- `isolated_node`: A argues where the node belongs; B argues another placement
  or that it should remain isolated; AB decides integration.
- `thin_domain`: A proposes the highest-value missing knowledge; B proposes an
  alternative or argues the gap is not worth filling yet; AB decides.

## Candidate Actions

Each candidate proposes one action:

- `create_node`: store a new durable finding/theory/synthesis.
- `update_target`: update the target node with the resolved insight.
- `resolve_no_change`: close the spark because existing knowledge is sufficient.
- `abandon`: close the spark as a false positive or low-value question.

## Manual and Background Triggers

- MCP `investigate_spark` calls this pipeline.
- `ResolutionScheduler` calls this pipeline.
- MCP `resolve_spark` remains a manual closeout primitive for agents that do
  their own investigation.
- MCP `abandon_spark` remains a manual false-positive closeout primitive.
- The same pipeline runs on Anthropic-compatible or OpenAI-compatible LLM
  endpoints through `NeoLLMClient`. Prompts request strict JSON; parsing
  tolerates common wrappers such as Markdown fences and surrounding prose.
- Local models are supported through OpenAI-compatible servers, but the judged
  debate/synthesis workflow is quality-sensitive. Prefer a strong instruction
  model for autonomous background resolution.

## Visualizer Rules

- `/api/graph` returns active sparks only in `sparks`.
- `/api/graph` returns resolved spark counts by resolved node in
  `spark_node_counts`.
- The graph renders active sparks as spark pseudo-nodes.
- Resolved sparks are not rendered as pseudo-nodes.
- Nodes with resolved spark counts blend toward gold and gain physical link
  strength through those resolved counts.
