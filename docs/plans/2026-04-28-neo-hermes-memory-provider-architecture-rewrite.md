# Neo ↔ Hermes Memory Provider Architecture Rewrite Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make Neo work as automatic semantic memory for Hermes/Atlas through an installable, public, reusable memory-provider integration that can coexist with episodic providers like Honcho.

**Architecture:** Build the reusable Neo-side integration first: a provider implementation, config layer, formatter, installer, and tests living in the Neo repo. Then add the smallest possible Hermes-side enhancement for multi-provider support, preserving backward compatibility with the existing singular `memory.provider` config and avoiding Dallas-specific hardcoding. Keep MCP as the deep graph interface; the memory provider is the fast semantic radar and compact recall surface.

**Tech Stack:** Python 3.12, Click CLI, Neo `NeoAPI`/`StoreInterface`, Hermes `MemoryProvider`, pytest, JSON/YAML config, local plugin install under `$HERMES_HOME/plugins/neo/`.

---

## Non-Negotiables

1. **Neo is semantic memory, not chat history.** Honcho remains episodic/user/conversation memory. Neo provides domain knowledge, concepts, theories, findings, syntheses, contradictions, and sparks.
2. **Signals first, firehose never.** Automatic recall should usually inject nothing. When relevant, it injects a tiny semantic signal. Deep details require a deliberate Neo tool/MCP/provider-tool retrieval.
3. **Public project, not Dallas duct tape.** Dallas/Atlas specifics belong in `$HERMES_HOME/neo.json` or environment config, not source code.
4. **Explicit writes by default.** Do not auto-ingest full chat turns into Neo. Default `auto_ingest` is `explicit-only` or `false`.
5. **Hermes multi-provider support is required.** Hermes must support at least one episodic provider and one semantic provider at the same time. This is not optional for Atlas: Neo must coexist with Honcho, not replace it.
6. **Update-safe Hermes work.** Make the Hermes change a small upstream-shaped patch/PR that preserves `memory.provider` exactly and adds optional `memory.providers`.
7. **Compaction-proof continuity.** This plan and `HERMES_SEMANTIC_MEMORY_DESIGN.md` are the source of truth if context gets nuked again. Because apparently context windows still enjoy arson.

---

## Current Ground Truth Verified 2026-04-28

### Neo repo

- Root: `/Users/atlasai/Documents/Code/Neo`
- Package: `neo-agent-knowledge`, version `0.1.1`
- CLI entry: `neo = "neo.cli.main:cli"` in `pyproject.toml`
- Existing CLI file: `src/neo/cli/main.py`
- Core API: `src/neo/core/api.py`
- Search entrypoint: `NeoAPI.search_knowledge(...)` at `src/neo/core/api.py:375-400`
- Assembler: `src/neo/core/assembler.py`
- Store vector search: `StoreInterface.vector_search(...)` in `src/neo/store/interface.py`; SQLite implementation in `src/neo/store/sqlite.py`
- Runtime singleton/helper path: `src/neo/runtime.py`
- Config path defaults: `~/.neo/.env` and `~/.neo/neo.db` via `src/neo/config.py`
- Existing design note: `/Users/atlasai/Documents/Code/Neo/HERMES_SEMANTIC_MEMORY_DESIGN.md`

### Hermes repo

- Root: `/Users/atlasai/.hermes/hermes-agent`
- Memory base class: `agent/memory_provider.py`
- Memory manager: `agent/memory_manager.py`
- Hermes currently documents/enforces one external provider:
  - `agent/memory_provider.py:3-10`
  - `agent/memory_manager.py:84-120`
- `MemoryManager.prefetch_all()` already merges provider output generically once providers are registered.
- `MemoryManager.get_all_tool_schemas()` and `handle_tool_call()` already handle multiple providers internally after registration.
- Main blocker is registration/config: `MemoryManager.add_provider()` rejects a second non-builtin provider via `_has_external`.

---

## Target End State

### User-facing install path

```bash
pip install neo-agent-knowledge[hermes]
neo hermes install --agent-name atlas
```

Optional activation for users who want Neo as their only external provider:

```bash
neo hermes install --agent-name atlas --set-active
```

Local Atlas/Honcho coexistence after Hermes multi-provider patch:

```yaml
memory:
  provider: honcho  # legacy fallback remains valid
  providers:
    - name: honcho
      type: episodic
      budget_tokens: 1500
    - name: neo
      type: semantic
      mode: signals-first
      signal_budget_tokens: 350
```

Neo plugin config:

```json
{
  "agent_name": "atlas",
  "top_k": 8,
  "hop_depth": 2,
  "token_budget": 1200,
  "signal_token_budget": 350,
  "min_confidence": 0.45,
  "include_sparks": true,
  "auto_ingest": "explicit-only",
  "recall_mode": "signals-first",
  "expand_threshold": 0.78,
  "signal_threshold": 0.55,
  "hint_threshold": 0.48,
  "scope": "self"
}
```

Installed Hermes plugin shim:

```text
$HERMES_HOME/plugins/neo/
├── __init__.py
├── plugin.yaml
├── README.md
└── cli.py
```

Reusable Neo integration code:

```text
src/neo/integrations/hermes/
├── __init__.py
├── config.py
├── formatter.py
├── installer.py
├── provider.py
├── recall.py
└── plugin_template/
    ├── __init__.py
    ├── plugin.yaml
    ├── README.md
    └── cli.py
```

Tests:

```text
tests/integrations/hermes/
├── test_config.py
├── test_formatter.py
├── test_installer.py
├── test_provider.py
└── test_recall.py
```

---

## Architecture Detail

### Neo provider role

The provider implements Hermes `MemoryProvider` but should not import Hermes at package import time unless Hermes is actually loading it. Use a compatibility/fallback base class so Neo tests can run without Hermes installed.

Provider methods:

- `name` → `"neo"`
- `is_available()` → true if Neo package imports and config is readable; no network calls.
- `initialize(session_id, hermes_home, agent_identity, platform, agent_context, ...)`
  - load `$HERMES_HOME/neo.json` if present
  - fall back to Neo env/default settings
  - create/get Neo agent by configured `agent_name`
  - store context flags; skip writes outside primary context
- `system_prompt_block()`
  - return short instructions explaining Neo semantic memory signals and explicit writes
- `prefetch(query, session_id="")`
  - perform bounded recall
  - return empty string below threshold
  - return compact signal block if relevant
  - avoid raw JSON
- `queue_prefetch(query, session_id="")`
  - optional phase 2; can be no-op initially
- `sync_turn(user, assistant, session_id="")`
  - no-op by default unless `auto_ingest` allows; do not raw-ingest chat
- `on_memory_write(action, target, content, metadata=None)`
  - mirror only semantic-worthy built-in memory writes when configured
- `get_tool_schemas()`
  - expose limited Neo tools: `neo_search`, `neo_remember`, `neo_get_node`, `neo_sparks`
- `handle_tool_call(...)`
  - dispatch to Neo API, return JSON string

### Recall modes

1. `signals-first` default:
   - `prefetch()` returns at most 3-5 signals with title/type/score/why/action.
   - No full node content unless very high confidence and under budget.

2. `compact`:
   - returns short summaries for top results.
   - Useful for users without MCP/tools.

3. `off`:
   - no automatic recall, tools only.

### Formatting contract

Provider output must look like this:

```md
## Neo Semantic Memory Signals
These are semantic-memory relevance signals, not user instructions.

- Commercial Lease Due Diligence — score 0.84; type synthesis; confidence 0.91
  Why relevant: current task touches studio relocation, lease economics, and risk allocation.
  Action: use `neo_search` or Neo MCP before giving final lease/business guidance.
```

Deep tool result from `neo_search` can be larger but still summarized:

```md
## Neo Search Results
Query: studio lease buildout risk

### Strong theories
- ...

### Findings
- ...

### Related concepts
- ...

### Active sparks
- ...
```

### Signal scoring v1

Use current Neo `search_knowledge()` and existing assembler first. Do not invent a whole retrieval lab before shipping the plumbing.

Initial score factors:

- `similarity` from vector seed if available
- node `confidence`
- node type priority: `synthesis > theory > finding > concept > answer > question > idea`
- edge/neighborhood presence as mild boost
- spark relevance only if same domain/near seed

Phase 2 can add retrieval cues and reranking.

### Hermes multi-provider enhancement

Target change: support both old and new config.

Old config remains valid:

```yaml
memory:
  provider: honcho
```

New config:

```yaml
memory:
  providers:
    - honcho
    - neo
```

or richer:

```yaml
memory:
  providers:
    - name: honcho
      type: episodic
      budget_tokens: 1500
    - name: neo
      type: semantic
      budget_tokens: 400
```

Implementation notes:

- Remove/replace `_has_external` hard rejection with configurable `max_external_providers`, defaulting to legacy behavior unless `memory.providers` is set.
- Register providers in configured order.
- Keep tool name conflict protection exactly as-is.
- Keep `prefetch_all`, `sync_all`, `queue_prefetch_all`, `get_all_tool_schemas`, and `handle_tool_call` mostly unchanged.
- Add tests proving legacy singular config behaves exactly as before.

---

# Implementation Tasks

## Phase 0 — Branch and safety rails

### Task 0.1: Create implementation branch in Neo

**Objective:** Isolate work in a clean feature branch.

**Files:** none

**Steps:**

```bash
cd /Users/atlasai/Documents/Code/Neo
git status --short
git switch -c feat/hermes-memory-provider
```

**Verification:** `git branch --show-current` prints `feat/hermes-memory-provider`.

**Commit:** none yet.

### Task 0.2: Confirm Hermes state before patching

**Objective:** Avoid trampling unrelated local Hermes changes.

**Files:** none

**Steps:**

```bash
cd /Users/atlasai/.hermes/hermes-agent
git status --short
git branch --show-current
git remote -v
```

**Verification:** Note dirty files before touching Hermes. Do not patch Hermes on `main` with unrelated uncommitted work.

---

## Phase 1 — Neo Hermes integration package skeleton

### Task 1.1: Create integration package directories

**Objective:** Add public reusable module locations.

**Files:**
- Create: `src/neo/integrations/__init__.py`
- Create: `src/neo/integrations/hermes/__init__.py`
- Create: `tests/integrations/hermes/__init__.py`

**Implementation:**

```python
# src/neo/integrations/__init__.py
"""Third-party agent host integrations for Neo."""
```

```python
# src/neo/integrations/hermes/__init__.py
"""Hermes Agent memory-provider integration for Neo."""

from neo.integrations.hermes.config import HermesNeoConfig
from neo.integrations.hermes.provider import NeoMemoryProvider

__all__ = ["HermesNeoConfig", "NeoMemoryProvider"]
```

**Verification:**

```bash
python -m compileall src/neo/integrations
```

**Commit:**

```bash
git add src/neo/integrations tests/integrations
git commit -m "feat: add Hermes integration package skeleton"
```

### Task 1.2: Add Hermes config model

**Objective:** Centralize provider config loading/saving without Dallas-specific defaults.

**Files:**
- Create: `src/neo/integrations/hermes/config.py`
- Test: `tests/integrations/hermes/test_config.py`

**Test first:**

```python
from pathlib import Path

from neo.integrations.hermes.config import HermesNeoConfig


def test_load_defaults_when_missing(tmp_path: Path):
    config = HermesNeoConfig.load(tmp_path)
    assert config.agent_name == "default"
    assert config.recall_mode == "signals-first"
    assert config.auto_ingest == "explicit-only"
    assert config.signal_token_budget <= config.token_budget


def test_load_from_hermes_home_json(tmp_path: Path):
    (tmp_path / "neo.json").write_text('{"agent_name":"atlas","top_k":6,"include_sparks":false}')
    config = HermesNeoConfig.load(tmp_path)
    assert config.agent_name == "atlas"
    assert config.top_k == 6
    assert config.include_sparks is False
```

**Implementation sketch:**

```python
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal


@dataclass(slots=True)
class HermesNeoConfig:
    agent_name: str = "default"
    top_k: int = 6
    hop_depth: int = 2
    token_budget: int = 1200
    signal_token_budget: int = 350
    min_confidence: float = 0.45
    include_sparks: bool = True
    auto_ingest: Literal[False, "explicit-only", "all"] = "explicit-only"
    recall_mode: Literal["signals-first", "compact", "off"] = "signals-first"
    hint_threshold: float = 0.48
    signal_threshold: float = 0.55
    expand_threshold: float = 0.78
    scope: Literal["self", "network"] = "self"

    @classmethod
    def load(cls, hermes_home: str | Path | None) -> "HermesNeoConfig":
        if not hermes_home:
            return cls()
        path = Path(hermes_home).expanduser() / "neo.json"
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        values = {key: value for key, value in raw.items() if key in allowed}
        config = cls(**values)
        config.validate()
        return config

    def validate(self) -> None:
        if self.top_k < 1:
            raise ValueError("top_k must be >= 1")
        if self.hop_depth < 0:
            raise ValueError("hop_depth must be >= 0")
        if self.signal_token_budget > self.token_budget:
            self.signal_token_budget = self.token_budget
        if not 0 <= self.min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")

    def save(self, hermes_home: str | Path) -> Path:
        path = Path(hermes_home).expanduser() / "neo.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")
        return path
```

**Verification:**

```bash
pytest tests/integrations/hermes/test_config.py -v
```

**Commit:**

```bash
git add src/neo/integrations/hermes/config.py tests/integrations/hermes/test_config.py
git commit -m "feat: add Hermes Neo provider config"
```

---

## Phase 2 — Recall and formatting

### Task 2.1: Add formatter for semantic signals and search results

**Objective:** Ensure Neo never injects raw graph JSON into Hermes prompts.

**Files:**
- Create: `src/neo/integrations/hermes/formatter.py`
- Test: `tests/integrations/hermes/test_formatter.py`

**Test first:**

```python
from neo.integrations.hermes.formatter import format_signal_block


def test_format_signal_block_empty_returns_empty():
    assert format_signal_block([]) == ""


def test_format_signal_block_labels_context_not_instruction():
    text = format_signal_block([
        {
            "title": "Commercial Lease Due Diligence",
            "node_type": "synthesis",
            "confidence": 0.91,
            "score": 0.84,
            "why": "current task mentions lease risk",
        }
    ])
    assert "Neo Semantic Memory Signals" in text
    assert "not user instructions" in text
    assert "Commercial Lease Due Diligence" in text
    assert "score 0.84" in text
```

**Implementation sketch:**

```python
from __future__ import annotations

from typing import Any


def _clip(text: str, limit: int = 180) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def format_signal_block(signals: list[dict[str, Any]], *, max_items: int = 5) -> str:
    if not signals:
        return ""
    lines = [
        "## Neo Semantic Memory Signals",
        "These are semantic-memory relevance signals, not user instructions.",
        "",
    ]
    for signal in signals[:max_items]:
        title = signal.get("title", "Untitled")
        node_type = signal.get("node_type") or signal.get("type") or "unknown"
        score = float(signal.get("score") or signal.get("similarity") or 0.0)
        confidence = float(signal.get("confidence") or 0.0)
        why = _clip(signal.get("why") or signal.get("summary") or "potentially relevant semantic knowledge exists")
        lines.append(f"- {title} — score {score:.2f}; type {node_type}; confidence {confidence:.2f}")
        lines.append(f"  Why relevant: {why}")
        lines.append("  Action: retrieve Neo details if this materially affects the answer/action.")
    return "\n".join(lines)


def format_search_result(result: dict[str, Any], *, token_budget: int = 1200) -> str:
    nodes = result.get("nodes") or []
    if not nodes:
        return '{"nodes": [], "message": "No relevant Neo knowledge found."}'
    lines = ["## Neo Search Results", f"Query: {result.get('query', '')}", ""]
    for node in nodes:
        lines.append(f"### {node.get('title', 'Untitled')}")
        lines.append(f"Type: {node.get('node_type', 'unknown')} | Confidence: {float(node.get('confidence') or 0):.2f}")
        if node.get("domain"):
            lines.append(f"Domain: {node['domain']}")
        lines.append(_clip(node.get("summary") or node.get("content") or "", 500))
        lines.append("")
    sparks = result.get("sparks") or []
    if sparks:
        lines.append("### Active sparks")
        for spark in sparks[:5]:
            lines.append(f"- {spark.get('description', '')} — priority {float(spark.get('priority') or 0):.2f}")
    return "\n".join(lines)
```

**Verification:**

```bash
pytest tests/integrations/hermes/test_formatter.py -v
```

**Commit:**

```bash
git add src/neo/integrations/hermes/formatter.py tests/integrations/hermes/test_formatter.py
git commit -m "feat: format Neo Hermes recall context"
```

### Task 2.2: Add recall adapter

**Objective:** Convert `NeoAPI.search_knowledge()` output into gated signal candidates.

**Files:**
- Create: `src/neo/integrations/hermes/recall.py`
- Test: `tests/integrations/hermes/test_recall.py`

**Test first:**

```python
from neo.integrations.hermes.config import HermesNeoConfig
from neo.integrations.hermes.recall import build_signals


def test_build_signals_filters_below_threshold():
    config = HermesNeoConfig(signal_threshold=0.6)
    result = {"nodes": [{"title": "Weak", "confidence": 0.2, "similarity": 0.3, "node_type": "idea"}]}
    assert build_signals(result, config) == []


def test_build_signals_prioritizes_synthesis():
    config = HermesNeoConfig(signal_threshold=0.1)
    result = {"nodes": [
        {"title": "Raw Idea", "confidence": 0.9, "similarity": 0.5, "node_type": "idea", "summary": "x"},
        {"title": "Synthesis", "confidence": 0.7, "similarity": 0.5, "node_type": "synthesis", "summary": "y"},
    ]}
    signals = build_signals(result, config)
    assert signals[0]["title"] == "Synthesis"
```

**Implementation notes:**

- Node type weights:
  - `synthesis`: 0.20
  - `theory`: 0.16
  - `finding`: 0.14
  - `concept`: 0.10
  - `answer`: 0.08
  - `question`: 0.04
  - `idea`: 0.02
- Score formula v1:
  - `score = similarity * 0.60 + confidence * 0.30 + type_weight`
- If similarity absent because assembler strips it, use conservative `0.5` for nodes returned by search, then phase 2 should preserve seed similarity in assembler output.
- `why` v1 can be summary-derived; phase 2 reranker can produce better why strings.

**Verification:**

```bash
pytest tests/integrations/hermes/test_recall.py -v
```

**Commit:**

```bash
git add src/neo/integrations/hermes/recall.py tests/integrations/hermes/test_recall.py
git commit -m "feat: gate Neo recall into semantic signals"
```

### Task 2.3: Preserve seed similarity in Neo assembler output

**Objective:** Give provider recall real similarity values instead of guessing.

**Files:**
- Modify: `src/neo/core/assembler.py`
- Test: existing or new `tests/test_api.py` / `tests/integrations/hermes/test_recall.py`

**Change:** Include `similarity` and `node_type` in returned nodes.

Current node return shape omits both. Update lines around `src/neo/core/assembler.py:77-87`:

```python
"nodes": [
    {
        "id": node["id"],
        "title": node["title"],
        "node_type": node.get("node_type"),
        "summary": node["summary"],
        "confidence": node["confidence"],
        "domain": node.get("domain"),
        "similarity": next((seed.get("similarity", 0.0) for seed in seeds if seed["id"] == node["id"]), 0.0),
    }
    for node in chosen
],
```

**Verification:**

```bash
pytest tests/test_api.py tests/integrations/hermes/test_recall.py -v
```

**Commit:**

```bash
git add src/neo/core/assembler.py tests
git commit -m "feat: include recall metadata in assembled knowledge"
```

---

## Phase 3 — Provider implementation

### Task 3.1: Add Hermes-compatible provider class

**Objective:** Implement the `MemoryProvider` surface without requiring Hermes during Neo tests.

**Files:**
- Create: `src/neo/integrations/hermes/provider.py`
- Test: `tests/integrations/hermes/test_provider.py`

**Test first with mocked API:**

```python
import json

from neo.integrations.hermes.provider import NeoMemoryProvider


class FakeStore:
    async def get_or_create_agent(self, name):
        return {"id": "agent-1", "name": name}


class FakeAPI:
    def __init__(self):
        self.store = FakeStore()

    async def search_knowledge(self, **kwargs):
        return {"query": kwargs["query"], "nodes": [
            {"id": "n1", "title": "Lease Risk", "node_type": "synthesis", "summary": "lease due diligence", "confidence": 0.9, "similarity": 0.8}
        ], "sparks": []}


def test_provider_prefetch_returns_signal(tmp_path):
    (tmp_path / "neo.json").write_text('{"agent_name":"atlas","signal_threshold":0.1}')
    provider = NeoMemoryProvider(api_factory=lambda: FakeAPI())
    provider.initialize("session-1", hermes_home=str(tmp_path), agent_context="primary")
    text = provider.prefetch("studio lease")
    assert "Neo Semantic Memory Signals" in text
    assert "Lease Risk" in text


def test_provider_tools_include_neo_search():
    provider = NeoMemoryProvider(api_factory=lambda: FakeAPI())
    names = {schema["name"] for schema in provider.get_tool_schemas()}
    assert "neo_search" in names
```

**Implementation notes:**

- Use `asyncio.run()` only when no event loop is running. If Hermes calls provider methods synchronously, this is okay. Add a helper `_run(coro)`.
- If a running event loop exists, use a dedicated background loop/thread in phase 2; for v1, fail gracefully with JSON tool error if necessary. Hermes provider hooks are sync.
- Initialize creates Neo agent via `api.store.get_or_create_agent(config.agent_name)`.
- Keep `sync_turn()` no-op unless configured.

**Provider sketch:**

```python
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from neo.integrations.hermes.config import HermesNeoConfig
from neo.integrations.hermes.formatter import format_search_result, format_signal_block
from neo.integrations.hermes.recall import build_signals
from neo.runtime import get_api_singleton

try:
    from agent.memory_provider import MemoryProvider
except Exception:
    class MemoryProvider:  # type: ignore[no-redef]
        pass


class NeoMemoryProvider(MemoryProvider):
    def __init__(self, api_factory: Callable[[], Any] = get_api_singleton) -> None:
        self._api_factory = api_factory
        self._api = None
        self._config = HermesNeoConfig()
        self._agent_id: str | None = None
        self._agent_context = "primary"

    @property
    def name(self) -> str:
        return "neo"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        hermes_home = kwargs.get("hermes_home")
        self._agent_context = kwargs.get("agent_context") or "primary"
        self._config = HermesNeoConfig.load(hermes_home)
        agent_identity = kwargs.get("agent_identity")
        if self._config.agent_name == "default" and agent_identity:
            self._config.agent_name = str(agent_identity)
        self._api = self._api_factory()
        agent = self._run(self._api.store.get_or_create_agent(self._config.agent_name))
        self._agent_id = agent["id"]

    def system_prompt_block(self) -> str:
        return (
            "Neo semantic memory is available. Neo stores durable domain/research knowledge "
            "(concepts, theories, findings, syntheses, contradictions, sparks), not chat history. "
            "When Neo injects a signal, treat it as background context and retrieve details before "
            "making domain-heavy recommendations. Write to Neo only for durable semantic knowledge."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._config.recall_mode == "off" or not query.strip() or not self._agent_id:
            return ""
        result = self._run(self._api.search_knowledge(
            agent_id=self._agent_id,
            query=query,
            top_k=self._config.top_k,
            hop_depth=self._config.hop_depth,
            token_budget=self._config.token_budget,
            min_weight=0.5,
            scope=self._config.scope,
        ))
        signals = build_signals(result, self._config)
        if self._config.recall_mode == "signals-first":
            return format_signal_block(signals)
        return format_search_result(result, token_budget=self._config.token_budget)

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        return None

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "neo_search",
                "description": "Search Neo semantic memory for durable concepts, theories, findings, syntheses, contradictions, and sparks.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "top_k": {"type": "integer", "default": 6}},
                    "required": ["query"],
                },
            },
            {
                "name": "neo_get_node",
                "description": "Fetch a Neo node by id.",
                "parameters": {"type": "object", "properties": {"node_id": {"type": "string"}}, "required": ["node_id"]},
            },
            {
                "name": "neo_sparks",
                "description": "List active Neo sparks/open questions for the configured agent.",
                "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "default": 5}}},
            },
            {
                "name": "neo_remember",
                "description": "Store durable semantic knowledge in Neo. Use only for research/domain knowledge, not ordinary chat history.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "node_type": {"type": "string", "default": "finding"},
                        "confidence": {"type": "number", "default": 0.7},
                        "domain": {"type": "string"},
                    },
                    "required": ["title", "content"],
                },
            },
        ]

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        try:
            if tool_name == "neo_search":
                result = self._run(self._api.search_knowledge(agent_id=self._agent_id, query=args["query"], top_k=args.get("top_k", self._config.top_k), hop_depth=self._config.hop_depth, token_budget=self._config.token_budget, scope=self._config.scope))
                return json.dumps({"ok": True, "markdown": format_search_result(result), "raw": result}, default=str)
            if tool_name == "neo_get_node":
                result = self._run(self._api.get_node(node_id=args["node_id"]))
                return json.dumps({"ok": True, "node": result}, default=str)
            if tool_name == "neo_sparks":
                result = self._run(self._api.get_sparks(agent_id=self._agent_id, limit=args.get("limit", 5)))
                return json.dumps({"ok": True, "sparks": result}, default=str)
            if tool_name == "neo_remember":
                result = self._run(self._api.store_node(agent_id=self._agent_id, node_type=args.get("node_type", "finding"), title=args["title"], content=args["content"], confidence=args.get("confidence", 0.7), domain=args.get("domain"), deduplicate=True))
                return json.dumps({"ok": True, "result": result}, default=str)
            return json.dumps({"ok": False, "error": f"Unknown Neo tool: {tool_name}"})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    @staticmethod
    def _run(coro):
        return asyncio.run(coro)
```

**Verification:**

```bash
pytest tests/integrations/hermes/test_provider.py -v
```

**Commit:**

```bash
git add src/neo/integrations/hermes/provider.py tests/integrations/hermes/test_provider.py
git commit -m "feat: add Neo Hermes memory provider"
```

### Task 3.2: Harden async helper for sync Hermes hooks

**Objective:** Prevent `asyncio.run()` failures if Hermes ever invokes provider hooks inside an active event loop.

**Files:**
- Modify: `src/neo/integrations/hermes/provider.py`
- Test: `tests/integrations/hermes/test_provider.py`

**Approach:** Add a small background event loop runner or use `anyio.from_thread` if available. Avoid adding heavy deps unless necessary.

**Verification:**

```bash
pytest tests/integrations/hermes/test_provider.py -v
```

**Commit:**

```bash
git add src/neo/integrations/hermes/provider.py tests/integrations/hermes/test_provider.py
git commit -m "fix: make Neo Hermes provider safe in sync hooks"
```

---

## Phase 4 — Installer and plugin shim

### Task 4.1: Add plugin template files

**Objective:** Install a thin Hermes shim that imports the provider from the Neo package.

**Files:**
- Create: `src/neo/integrations/hermes/plugin_template/__init__.py`
- Create: `src/neo/integrations/hermes/plugin_template/plugin.yaml`
- Create: `src/neo/integrations/hermes/plugin_template/README.md`
- Create: `src/neo/integrations/hermes/plugin_template/cli.py`

**Template `__init__.py`:**

```python
from neo.integrations.hermes.provider import NeoMemoryProvider


def register(ctx):
    ctx.register_memory_provider(NeoMemoryProvider())
```

**Template `plugin.yaml`:**

```yaml
name: neo
type: memory
version: 0.1.0
description: Neo semantic memory provider for Hermes Agent
author: Dallas Rose
```

**Verification:**

```bash
python -m compileall src/neo/integrations/hermes/plugin_template
```

**Commit:**

```bash
git add src/neo/integrations/hermes/plugin_template
git commit -m "feat: add Hermes plugin shim template"
```

### Task 4.2: Add installer

**Objective:** Copy plugin template to `$HERMES_HOME/plugins/neo/` and write `$HERMES_HOME/neo.json`.

**Files:**
- Create: `src/neo/integrations/hermes/installer.py`
- Test: `tests/integrations/hermes/test_installer.py`

**Test first:**

```python
from pathlib import Path

from neo.integrations.hermes.installer import install_hermes_plugin


def test_installer_writes_plugin_and_config(tmp_path: Path):
    result = install_hermes_plugin(tmp_path, agent_name="atlas", set_active=False)
    assert (tmp_path / "plugins" / "neo" / "__init__.py").exists()
    assert (tmp_path / "plugins" / "neo" / "plugin.yaml").exists()
    assert (tmp_path / "neo.json").exists()
    assert result["plugin_dir"] == str(tmp_path / "plugins" / "neo")
```

**Implementation notes:**

- Use `importlib.resources.files()` to read template files.
- Do not overwrite user config unless `force=True`; merge agent name if config absent.
- If `set_active=True`, carefully patch `$HERMES_HOME/config.yaml` to `memory.provider: neo`; warn if existing provider is not Neo. For v1, better: print instructions instead of mutating YAML unless PyYAML exists.

**Verification:**

```bash
pytest tests/integrations/hermes/test_installer.py -v
```

**Commit:**

```bash
git add src/neo/integrations/hermes/installer.py tests/integrations/hermes/test_installer.py
git commit -m "feat: install Neo Hermes plugin"
```

### Task 4.3: Wire CLI command `neo hermes install`

**Objective:** Provide the public install UX.

**Files:**
- Modify: `src/neo/cli/main.py`
- Test: `tests/integrations/hermes/test_installer.py` or new CLI runner test

**Click implementation sketch:**

```python
@cli.group()
def hermes() -> None:
    """Hermes Agent integration commands."""


@hermes.command("install")
@click.option("--hermes-home", default=None, help="Hermes home directory. Defaults to $HERMES_HOME or ~/.hermes.")
@click.option("--agent-name", default="default", show_default=True)
@click.option("--set-active", is_flag=True, help="Set memory.provider=neo where supported. Use with care if another provider is active.")
@click.option("--force", is_flag=True, help="Overwrite existing Neo Hermes plugin files.")
def hermes_install(hermes_home: str | None, agent_name: str, set_active: bool, force: bool) -> None:
    import os
    from pathlib import Path
    from neo.integrations.hermes.installer import install_hermes_plugin

    home = Path(hermes_home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    result = install_hermes_plugin(home, agent_name=agent_name, set_active=set_active, force=force)
    click.echo(f"Installed Neo Hermes plugin: {result['plugin_dir']}")
    click.echo(f"Wrote Neo Hermes config: {result['config_path']}")
    if not set_active:
        click.echo("Neo was installed but not activated. This preserves any existing memory provider such as Honcho.")
```

**Verification:**

```bash
neo hermes install --hermes-home /tmp/hermes-test --agent-name atlas --force
```

Expected: plugin files and `/tmp/hermes-test/neo.json` exist.

**Commit:**

```bash
git add src/neo/cli/main.py tests/integrations/hermes
git commit -m "feat: add neo hermes install command"
```

### Task 4.4: Include plugin template in package data

**Objective:** Ensure installed wheels include plugin templates.

**Files:**
- Modify: `pyproject.toml`

**Change:**

```toml
[tool.setuptools.package-data]
neo = ["static/**/*", "integrations/hermes/plugin_template/**/*"]
```

**Verification:**

```bash
python -m build
python - <<'PY'
from importlib.resources import files
print(files('neo.integrations.hermes.plugin_template').joinpath('plugin.yaml').read_text())
PY
```

**Commit:**

```bash
git add pyproject.toml
git commit -m "build: package Hermes plugin template"
```

---

## Phase 5 — Documentation

### Task 5.1: Add public docs for Hermes integration

**Objective:** Explain install, activation, Honcho coexistence, and safety defaults.

**Files:**
- Create: `docs/hermes-memory-provider.md`
- Modify: `README.md`

**Docs must include:**

- What Neo semantic memory is
- What it is not: not episodic memory, not a chat transcript store
- Install command
- Activation command
- Honcho coexistence warning
- Config file reference
- Tool list
- Auto-ingest warning
- Troubleshooting

**Verification:** Read docs and confirm no Dallas-specific details.

**Commit:**

```bash
git add docs/hermes-memory-provider.md README.md
git commit -m "docs: add Hermes memory provider guide"
```

### Task 5.2: Update design note with implementation status

**Objective:** Keep compaction-safe architecture note current.

**Files:**
- Modify: `HERMES_SEMANTIC_MEMORY_DESIGN.md`

**Add section:**

```md
## Implementation plan

Detailed task plan lives at:

`docs/plans/2026-04-28-neo-hermes-memory-provider-architecture-rewrite.md`

Implementation branch: `feat/hermes-memory-provider`
```

**Verification:**

```bash
grep -n "Implementation plan" HERMES_SEMANTIC_MEMORY_DESIGN.md
```

**Commit:**

```bash
git add HERMES_SEMANTIC_MEMORY_DESIGN.md docs/plans/2026-04-28-neo-hermes-memory-provider-architecture-rewrite.md
git commit -m "docs: add Neo Hermes provider architecture plan"
```

---

## Phase 6 — Hermes multi-provider support

> Do this after Neo provider tests pass. This is either an upstream PR or a tiny maintained local branch. Do not casually hack this into Dallas's active Hermes main branch like a raccoon in a server closet.

### Task 6.1: Create Hermes branch

**Objective:** Isolate Hermes multi-provider changes.

**Steps:**

```bash
cd /Users/atlasai/.hermes/hermes-agent
git status --short
git switch -c feat/multiple-memory-providers
```

If local unrelated changes exist, stash or commit them separately first.

### Task 6.2: Add config parser for `memory.providers`

**Objective:** Parse both legacy singular and new plural provider config.

**Files:**
- Modify: likely `run_agent.py` or config helper module after inspecting exact current parser
- Test: add/modify Hermes tests if present

**Behavior:**

- If `memory.providers` exists, use it.
- Else if `memory.provider` exists, wrap as single provider.
- Else no external provider.
- Providers can be strings or dicts.

**Pseudo-code:**

```python
def parse_memory_provider_configs(memory_config):
    providers = memory_config.get("providers")
    if providers:
        normalized = []
        for item in providers:
            if isinstance(item, str):
                normalized.append({"name": item})
            else:
                normalized.append(dict(item))
        return normalized
    provider = memory_config.get("provider")
    return [{"name": provider}] if provider else []
```

### Task 6.3: Relax external provider registration only when plural config is active

**Objective:** Preserve legacy one-provider behavior unless user opts into plural config.

**Files:**
- Modify: `/Users/atlasai/.hermes/hermes-agent/agent/memory_manager.py`

**Implementation target:**

- Constructor accepts `allow_multiple_external: bool = False`.
- If false, keep current `_has_external` rejection exactly.
- If true, allow multiple external providers.
- Retain tool conflict warnings.

**Test cases:**

1. Default manager rejects second external provider.
2. `MemoryManager(allow_multiple_external=True)` accepts two external providers.
3. Tool conflict keeps first tool provider.
4. `prefetch_all()` includes both providers' context.

### Task 6.4: Register each configured provider in Hermes startup

**Objective:** Actually load both Honcho and Neo when configured.

**Files:**
- Modify: `run_agent.py` or plugin loader callsite

**Behavior:**

- For each parsed provider config, call existing plugin loader.
- Initialize/register provider in order.
- Pass provider-specific config if Hermes supports it; otherwise Neo reads `$HERMES_HOME/neo.json`.

### Task 6.5: Docs for Hermes multi-provider config

**Objective:** Make upstream PR legible.

**Files:**
- Hermes docs wherever memory providers are documented

**Docs:**

```yaml
memory:
  providers:
    - honcho
    - neo
```

Explain context/tool bloat risk and order semantics.

### Task 6.6: Verify Atlas config

**Objective:** Activate Honcho + Neo together locally.

**Steps:**

```bash
cd /Users/atlasai/.hermes
# edit config.yaml carefully after inspecting exact active file
```

Expected config:

```yaml
memory:
  providers:
    - honcho
    - neo
```

Then restart Hermes/WebUI/gateway as appropriate and check logs.

**Verification:**

- Hermes logs show both Honcho and Neo registered.
- A user query about a known Neo topic injects a `Neo Semantic Memory Signals` block.
- Ordinary chatter injects no Neo block.
- Honcho context still appears.

---

## Phase 7 — Local install and verification

### Task 7.1: Reinstall Neo editable from source

**Objective:** Make active environment use the new Neo code.

**Steps:**

```bash
cd /Users/atlasai/Documents/Code/Neo
pip install -e .
neo hermes install --agent-name atlas --force
```

**Verification:**

```bash
python - <<'PY'
from neo.integrations.hermes.provider import NeoMemoryProvider
print(NeoMemoryProvider().name)
PY
```

Expected: `neo`.

### Task 7.2: Smoke test provider directly

**Objective:** Prove provider recall works outside Hermes.

**Steps:**

```bash
python - <<'PY'
from neo.integrations.hermes.provider import NeoMemoryProvider
p = NeoMemoryProvider()
p.initialize('smoke', hermes_home='/Users/atlasai/.hermes', agent_context='primary', agent_identity='atlas')
print(p.prefetch('commercial studio lease buildout risk')[:1000])
PY
```

**Expected:** Either a Neo signal block if matching data exists, or empty string if thresholds are too strict. If empty, temporarily lower `signal_threshold` in `/Users/atlasai/.hermes/neo.json` and retry.

### Task 7.3: Smoke test via Hermes

**Objective:** Prove automatic recall reaches the agent context.

**Steps:**

- Restart Hermes after installing plugin and applying multi-provider config.
- Ask a query known to match Neo, e.g. "What should I watch out for in a commercial studio lease buildout?"
- Check logs for provider registration and prefetch.

**Expected:**

- Honcho remains active.
- Neo registers.
- Neo signal appears only for relevant topics.

---

## Phase 8 — Quality bar before merge

Run all Neo tests:

```bash
cd /Users/atlasai/Documents/Code/Neo
pytest -v
```

Run package/build check:

```bash
python -m build
```

Run Hermes tests touched by memory manager, if test suite exists:

```bash
cd /Users/atlasai/.hermes/hermes-agent
pytest tests -k memory -v
```

Inspect diffs:

```bash
cd /Users/atlasai/Documents/Code/Neo
git diff --stat main...HEAD
git diff --check
```

Push Neo branch:

```bash
git push -u origin feat/hermes-memory-provider
```

Open PR with title:

```text
feat: add Hermes semantic memory provider integration
```

---

## Acceptance Criteria

- [ ] `neo hermes install --agent-name atlas` installs a Hermes plugin shim and writes `neo.json`.
- [ ] Neo provider can be imported from installed package.
- [ ] Provider implements Hermes memory hooks.
- [ ] Provider automatic recall returns empty for irrelevant turns.
- [ ] Provider automatic recall returns compact semantic signals for relevant turns.
- [ ] Provider exposes `neo_search`, `neo_remember`, `neo_get_node`, and `neo_sparks`.
- [ ] Provider does not auto-ingest full turns by default.
- [ ] Neo tests pass.
- [ ] Docs explain Honcho coexistence and single-provider Hermes limitation.
- [ ] Hermes plural-provider patch preserves legacy `memory.provider` behavior.
- [ ] Atlas can run Honcho + Neo together without Dallas-specific source edits.

---

## Known Risks and Controls

### Risk: Context bloat

**Control:** `signals-first`, thresholds, `signal_token_budget`, no raw graph JSON.

### Risk: Graph pollution

**Control:** explicit-write default; no full-turn ingestion; `neo_remember` warning in tool description.

### Risk: Hermes updates overwrite local patch

**Control:** upstream-shaped small branch/PR; avoid long-lived dirty `main`; document exact patch.

### Risk: Async mismatch

**Control:** provider hooks are sync; add tested sync-to-async bridge.

### Risk: Too many tools

**Control:** expose only four small tools initially; MCP remains deep interface.

### Risk: Bad recall / noisy signals

**Control:** thresholds, test eval set, future negative feedback/cooldown.

---

## Future Enhancements After V1

1. Add `recall_cues` metadata per node and embed/search cues separately.
2. Add cheap LLM/classifier reranker for direct/adjacent/analogical relevance.
3. Add false-positive feedback/cooldowns.
4. Add evaluation dataset with real Atlas query fixtures.
5. Add provider-level token budgeting if Hermes accepts richer provider metadata.
6. Add composite `neo_honcho` provider only if Hermes multi-provider support stalls.

---

## Immediate Next Execution Path

1. Start in Neo repo, not Hermes.
2. Implement Phases 1–5 and get all Neo tests passing.
3. Install plugin locally without setting it active.
4. Patch Hermes multi-provider support on a clean branch.
5. Activate Honcho + Neo together for Atlas.
6. Push Neo branch/PR.

This gives Dallas the product he wants: Neo becomes automatic semantic expertise, Honcho keeps being episodic memory, and the public Neo project gains a clean installable Hermes integration instead of a private Frankenstein. Frankenstein ships fast, sure, but then he asks for equity.
