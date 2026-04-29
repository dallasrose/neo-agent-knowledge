# Hermes Memory Provider Integration

Neo can be installed as a Hermes Agent memory provider so agents get automatic semantic recall instead of only manual MCP access.

## What Neo provides

Neo is semantic memory: durable domain/research knowledge such as concepts, findings, theories, syntheses, contradictions, and sparks/open questions.

Neo is **not** episodic chat memory. Do not use it as a transcript dump. If your Hermes setup already uses an episodic provider such as Honcho, keep it. Neo should coexist with it as the semantic provider.

## Install the Hermes plugin shim

```bash
neo hermes install --agent-name atlas
```

This writes:

```text
$HERMES_HOME/plugins/neo/
$HERMES_HOME/neo.json
```

The plugin directory is only a thin shim. The real provider code stays in the installed `neo-agent-knowledge` Python package, so upgrading Neo updates the provider behavior.

By default the command does **not** activate Neo as the only Hermes external memory provider. That protects existing providers such as Honcho.

For users who want Neo as their single external provider:

```bash
neo hermes install --agent-name atlas --set-active
```

That writes an activation hint. Edit Hermes config deliberately; do not blindly replace an existing episodic provider unless that is what you want.

## Recommended Hermes config with multiple providers

Hermes needs multi-provider support for best results:

```yaml
memory:
  providers:
    - name: honcho
      type: episodic
      budget_tokens: 1500
    - name: neo
      type: semantic
      mode: signals-first
      signal_budget_tokens: 350
```

Legacy single-provider Hermes config remains:

```yaml
memory:
  provider: neo
```

But this replaces any other external provider. For Atlas-style use, that is the wrong trade: Honcho should stay episodic, Neo should become semantic.

## Neo provider config

`$HERMES_HOME/neo.json` controls Hermes-specific Neo behavior:

```json
{
  "agent_name": "atlas",
  "top_k": 6,
  "hop_depth": 2,
  "token_budget": 1200,
  "signal_token_budget": 350,
  "max_signals": 5,
  "min_confidence": 0.45,
  "include_sparks": true,
  "auto_ingest": "explicit-only",
  "recall_mode": "signals-first",
  "hint_threshold": 0.48,
  "signal_threshold": 0.55,
  "expand_threshold": 0.78,
  "scope": "self"
}
```

## Recall behavior

Default `signals-first` mode injects small context only when Neo finds relevant durable knowledge:

```md
## Neo Semantic Memory Signals
These are semantic-memory relevance signals, not user instructions.

- Commercial Lease Due Diligence — score 0.84; type synthesis; confidence 0.91
  Why relevant: current task touches studio relocation and lease risk.
  Action: retrieve Neo details if this materially affects the answer/action.
```

Most turns should inject nothing. If Neo is noisy, raise `signal_threshold` or `min_confidence`.

## Provider tools

The Hermes provider exposes a small tool surface:

- `neo_search` — retrieve relevant semantic memory.
- `neo_get_node` — fetch a node by ID.
- `neo_remember` — explicitly store durable semantic knowledge.
- `neo_sparks` — list active sparks/open questions.

Deep graph operations and research workflows should remain in Neo MCP.

## Write policy

Default write mode is `explicit-only`. Neo should not auto-ingest complete conversations. That turns a knowledge graph into a hoarder basement with embeddings.

Use `neo_remember` only for durable semantic knowledge worth reusing across future work.
