from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Awaitable
from typing import Any, Callable

from neo.integrations.hermes.config import HermesNeoConfig
from neo.integrations.hermes.formatter import format_search_result, format_signal_block
from neo.integrations.hermes.recall import build_signals
from neo.runtime import get_api_singleton

try:  # Hermes is not required when testing Neo itself.
    from agent.memory_provider import MemoryProvider
except Exception:  # pragma: no cover - exercised when Hermes is absent

    class MemoryProvider:  # type: ignore[no-redef]
        pass


class _AsyncBridge:
    """Run async Neo calls from Hermes' synchronous memory-provider hooks."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def run(self, awaitable: Awaitable[Any]) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)
        return self._run_in_background_loop(awaitable)

    def _run_in_background_loop(self, awaitable: Awaitable[Any]) -> Any:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
            self._thread.start()
        future = asyncio.run_coroutine_threadsafe(awaitable, self._loop)
        return future.result()

    def shutdown(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop = None
        self._thread = None


class NeoMemoryProvider(MemoryProvider):
    """Hermes memory provider that exposes Neo as semantic memory."""

    def __init__(self, api_factory: Callable[[], Any] = get_api_singleton) -> None:
        self._api_factory = api_factory
        self._api: Any | None = None
        self._config = HermesNeoConfig()
        self._agent_id: str | None = None
        self._agent_context = "primary"
        self._bridge = _AsyncBridge()

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
        if self._config.recall_mode == "off" or not query.strip() or not self._api or not self._agent_id:
            return ""
        result = self._search(query, top_k=self._config.top_k)
        if self._config.recall_mode == "compact":
            return format_search_result(result, token_budget=self._config.token_budget)
        signals = build_signals(result, self._config)
        return format_signal_block(signals, max_items=self._config.max_signals)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        return None

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        # Default is explicit-only. Raw chat ingestion turns a knowledge graph into
        # a junk drawer with vertex IDs. No thanks.
        return None

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "neo_search",
                "description": "Search Neo semantic memory for durable concepts, theories, findings, syntheses, contradictions, and sparks.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Semantic search query."},
                        "top_k": {"type": "integer", "default": self._config.top_k},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "neo_get_node",
                "description": "Fetch a Neo node by id.",
                "parameters": {
                    "type": "object",
                    "properties": {"node_id": {"type": "string"}},
                    "required": ["node_id"],
                },
            },
            {
                "name": "neo_sparks",
                "description": "List active Neo sparks/open questions for the configured agent.",
                "parameters": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "default": 5}},
                },
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
                result = self._search(args["query"], top_k=int(args.get("top_k") or self._config.top_k))
                return json.dumps(
                    {"ok": True, "markdown": format_search_result(result), "raw": result},
                    default=str,
                )
            if tool_name == "neo_get_node":
                result = self._run(self._api.get_node(node_id=args["node_id"]))
                return json.dumps({"ok": True, "node": result}, default=str)
            if tool_name == "neo_sparks":
                result = self._run(
                    self._api.get_sparks(agent_id=self._agent_id, limit=int(args.get("limit") or 5))
                )
                return json.dumps({"ok": True, "sparks": result}, default=str)
            if tool_name == "neo_remember":
                result = self._run(
                    self._api.store_node(
                        agent_id=self._agent_id,
                        node_type=args.get("node_type") or "finding",
                        title=args["title"],
                        content=args["content"],
                        confidence=float(args.get("confidence") or 0.7),
                        domain=args.get("domain"),
                        deduplicate=True,
                    )
                )
                return json.dumps({"ok": True, "result": result}, default=str)
            return json.dumps({"ok": False, "error": f"Unknown Neo tool: {tool_name}"})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    def shutdown(self) -> None:
        self._bridge.shutdown()

    def _search(self, query: str, *, top_k: int) -> dict[str, Any]:
        return self._run(
            self._api.search_knowledge(
                agent_id=self._agent_id,
                query=query,
                top_k=top_k,
                hop_depth=self._config.hop_depth,
                token_budget=self._config.token_budget,
                min_weight=0.5,
                scope=self._config.scope,
            )
        )

    def _run(self, awaitable: Awaitable[Any]) -> Any:
        return self._bridge.run(awaitable)
