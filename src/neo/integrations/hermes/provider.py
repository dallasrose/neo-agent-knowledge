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
            "Semantic research memory is available through Neo. Treat it as your own durable "
            "research memory: concepts, theories, findings, syntheses, contradictions, and open "
            "questions, not chat history. When a signal appears, think of it as 'I remember "
            "researching something relevant' and retrieve details before making domain-heavy "
            "recommendations. When the user gives a source link to research or remember, use "
            "neo_ingest_url. Write only durable semantic knowledge, not ordinary conversation."
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
                "name": "neo_investigate_spark",
                "description": "Run Neo's intelligent spark investigation pipeline with internal recall, web search when configured, candidate resolutions, and a judge.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "spark_id": {"type": "string"},
                        "mode": {
                            "type": "string",
                            "enum": ["preview", "apply"],
                            "default": "apply",
                        },
                    },
                    "required": ["spark_id"],
                },
            },
            {
                "name": "neo_resolve_spark",
                "description": "Mark a spark/open question resolved after you have researched it and stored or identified the durable answer nodes.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "spark_id": {"type": "string"},
                        "node_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Neo node IDs that resolve this spark.",
                        },
                        "notes": {"type": "string"},
                    },
                    "required": ["spark_id"],
                },
            },
            {
                "name": "neo_abandon_spark",
                "description": "Close a spark/open question as not useful or not currently answerable.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "spark_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["spark_id"],
                },
            },
            {
                "name": "neo_ingest_url",
                "description": "Read a URL and store durable research findings in semantic memory. Use when the user asks you to research or remember a source link.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "title": {"type": "string"},
                        "domain": {"type": "string"},
                        "query_focus": {"type": "string"},
                        "preview": {"type": "boolean", "default": False},
                        "max_findings": {"type": "integer", "default": 0, "description": "Optional total ceiling. 0 or omitted means no artificial source-level cap; long sources are chunked and deduped."},
                    },
                    "required": ["url"],
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
            if tool_name == "neo_investigate_spark":
                result = self._investigate_spark(
                    spark_id=args["spark_id"],
                    mode=args.get("mode") or "apply",
                )
                return json.dumps({"ok": True, "result": result}, default=str)
            if tool_name == "neo_resolve_spark":
                result = self._run(
                    self._api.resolve_spark(
                        spark_id=args["spark_id"],
                        node_ids=args.get("node_ids") or [],
                        notes=args.get("notes"),
                    )
                )
                return json.dumps({"ok": True, "result": result}, default=str)
            if tool_name == "neo_abandon_spark":
                result = self._run(
                    self._api.abandon_spark(
                        spark_id=args["spark_id"],
                        reason=args.get("reason"),
                    )
                )
                return json.dumps({"ok": True, "result": result}, default=str)
            if tool_name == "neo_ingest_url":
                result = self._run(
                    self._api.ingest_source_url(
                        agent_id=self._agent_id,
                        url=args["url"],
                        title=args.get("title"),
                        domain=args.get("domain"),
                        query_focus=args.get("query_focus"),
                        preview=bool(args.get("preview") or False),
                        max_findings=(int(args["max_findings"]) if args.get("max_findings") is not None else None),
                    )
                )
                return json.dumps({"ok": True, "result": result}, default=str)
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

    def _investigate_spark(self, *, spark_id: str, mode: str) -> dict[str, Any]:
        if mode not in {"preview", "apply"}:
            raise ValueError("mode must be 'preview' or 'apply'")

        from neo.config import settings
        from neo.core.resolver import ResolutionLLM, SparkResolver
        from neo.core.web_search import NullWebSearch, WebSearchClient

        if not settings.llm_configured_for("resolution"):
            raise RuntimeError("Neo spark investigation requires NEO_LLM_MODEL plus a configured cloud key or local base URL")

        sparks = self._run(self._api.get_sparks(agent_id=self._agent_id, status="", limit=5000))
        spark = next((item for item in sparks if item.get("id") == spark_id), None)
        if spark is None:
            raise ValueError(f"Spark {spark_id} not found")
        agent = self._run(self._api.store.get_agent(self._agent_id))
        web_search = (
            WebSearchClient(settings.search_provider, settings.search_api_key)
            if settings.search_configured()
            else NullWebSearch()
        )
        llm = ResolutionLLM(
            api_key=settings.llm_api_key_for("resolution"),
            model=settings.llm_model_for("resolution"),
            base_url=settings.llm_base_url_for("resolution"),
            provider=settings.llm_provider_for("resolution"),
        )
        resolver = SparkResolver(self._api, llm, web_search)
        return self._run(resolver.resolve(spark, agent, mode=mode, trigger="hermes_memory"))

    def _run(self, awaitable: Awaitable[Any]) -> Any:
        return self._bridge.run(awaitable)
