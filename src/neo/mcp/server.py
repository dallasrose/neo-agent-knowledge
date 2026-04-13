from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP

from neo.config import settings
from neo.core.consolidation import ConsolidationEngine
from neo.core.scheduler import ConsolidationScheduler
from neo.db import init_db
from neo.runtime import ensure_default_agent, get_api_singleton

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


async def _contemplation_loop(api, agent_id: str) -> None:
    """Periodically scan the graph for interesting structural signals.

    Candidate selection strategy (in priority order):
      1. Recent nodes in the agent's core domains — fresh, on-topic knowledge
         most likely to have open questions worth investigating.
      2. Recent nodes outside core domains — new knowledge regardless of topic.
      3. Isolated nodes (no sparks at all, newest first) — anything that has
         never been examined for research gaps.

    Sparks are not required on every node — the LLM returns [] when nothing
    interesting emerges, which is correct behaviour.
    """
    from datetime import datetime, timedelta, timezone

    interval = settings.contemplation_interval_minutes * 60
    batch = settings.contemplation_batch_size
    while True:
        await asyncio.sleep(interval)
        try:
            agent = await api.store.get_agent(agent_id)
            if agent is None:
                continue

            core_domains: set[str] = set(agent.get("domains") or [])
            recent_cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

            # 1. Recent nodes in core domains (highest research value)
            recent_all = await api.store.get_nodes_by_agent(
                agent_id, since=recent_cutoff, limit=batch * 2
            )
            # Split into domain-aligned and other
            recent_on_topic = [
                n for n in recent_all
                if n.get("domain") and n["domain"] in core_domains
            ]
            recent_other = [
                n for n in recent_all
                if n not in recent_on_topic
            ]

            # 2. Isolated nodes with no sparks yet (newest first, from store)
            isolated = await api.store.get_nodes_without_sparks(
                agent_id, limit=batch
            )

            # Merge: on-topic recent → other recent → isolated, dedup
            seen: set[str] = set()
            candidates: list[dict] = []
            for node in [*recent_on_topic, *recent_other, *isolated]:
                if node["id"] not in seen and len(candidates) < batch:
                    seen.add(node["id"])
                    candidates.append(node)

            if candidates:
                on_topic_count = sum(
                    1 for n in candidates
                    if n.get("domain") and n["domain"] in core_domains
                )
                logger.info(
                    "Contemplation: %d candidates (%d on-topic, %d other)",
                    len(candidates), on_topic_count, len(candidates) - on_topic_count,
                )
            for node in candidates:
                try:
                    await api.spark_generator.generate_for_node(agent=agent, node=node)
                except Exception as exc:
                    logger.warning("Contemplation failed for node %s: %s", node.get("id"), exc)
        except Exception as exc:
            logger.warning("Contemplation loop error: %s", exc)


@asynccontextmanager
async def _lifespan(server: FastMCP):
    await init_db()
    api = get_api_singleton()
    agent = await ensure_default_agent(api)
    agent_id = agent["id"]

    tasks: list[asyncio.Task] = []

    if settings.consolidation_enabled:
        scheduler = ConsolidationScheduler(
            api.store,
            ConsolidationEngine(api.store),
            agent_id=agent_id,
            schedule=settings.consolidation_schedule,
            node_threshold=settings.consolidation_node_threshold,
            poll_interval_seconds=settings.scheduler_poll_interval_seconds,
        )
        tasks.append(scheduler.start())
        logger.info("Neo: consolidation scheduler started (schedule=%s)", settings.consolidation_schedule)

    if settings.contemplation_enabled:
        tasks.append(asyncio.create_task(_contemplation_loop(api, agent_id)))
        logger.info(
            "Neo: contemplation loop started (every %d min, batch=%d)",
            settings.contemplation_interval_minutes,
            settings.contemplation_batch_size,
        )

    if settings.resolution_enabled:
        from neo.core.web_search import WebSearchClient, NullWebSearch
        from neo.core.resolver import ResolutionLLM, SparkResolver
        from neo.core.resolution_scheduler import ResolutionScheduler

        web_search = (
            WebSearchClient(settings.search_provider, settings.search_api_key)
            if settings.search_api_key
            else NullWebSearch()
        )
        res_model = settings.llm_resolution_model or settings.llm_spark_model
        res_key = settings.llm_resolution_api_key or settings.llm_spark_api_key
        res_url = settings.llm_resolution_base_url or settings.llm_spark_base_url
        if res_key:
            resolution_llm = ResolutionLLM(api_key=res_key, model=res_model, base_url=res_url)
            resolver = SparkResolver(api, resolution_llm, web_search)
            res_sched = ResolutionScheduler(
                api, resolver, agent_id,
                interval_minutes=settings.resolution_interval_minutes,
                batch_size=settings.resolution_batch_size,
            )
            tasks.append(res_sched.start())
            logger.info("Neo: resolution scheduler started (every %dm)", settings.resolution_interval_minutes)
        else:
            logger.info("Neo: resolution scheduler: no LLM key, disabled")
    else:
        logger.info("Neo: resolution scheduler disabled (set NEO_RESOLUTION_ENABLED=true)")

    if settings.discovery_enabled:
        from neo.core.discovery import DiscoveryJob
        from neo.core.discovery_scheduler import DiscoveryScheduler
        from neo.core.youtube import YouTubeSearchClient, EchoSearchAsYouTube
        from neo.core.web_search import WebSearchClient, NullWebSearch

        # Build YouTube search client: Data API preferred, web search fallback
        yt_search = None
        if settings.youtube_api_key:
            yt_search = YouTubeSearchClient(settings.youtube_api_key)
            logger.info("Neo: YouTube search via Data API")
        elif settings.search_api_key:
            ws = WebSearchClient(settings.search_provider, settings.search_api_key)
            yt_search = EchoSearchAsYouTube(ws)
            logger.info("Neo: YouTube search via %s (web fallback)", settings.search_provider)
        else:
            logger.info("Neo: no YouTube search client — autonomous mode disabled. "
                        "Set NEO_YOUTUBE_API_KEY or NEO_SEARCH_API_KEY to enable.")

        # Reuse the resolution LLM for query generation if available
        res_model = settings.llm_resolution_model or settings.llm_spark_model
        res_key   = settings.llm_resolution_api_key or settings.llm_spark_api_key
        res_url   = settings.llm_resolution_base_url or settings.llm_spark_base_url
        discovery_llm = None
        if res_key:
            from neo.core.resolver import ResolutionLLM
            discovery_llm = ResolutionLLM(api_key=res_key, model=res_model, base_url=res_url)

        discovery_job = DiscoveryJob(api, llm=discovery_llm, yt_search=yt_search)
        discovery_sched = DiscoveryScheduler(
            api, discovery_job, agent_id,
            interval_minutes=settings.discovery_interval_minutes,
            batch_size=settings.discovery_batch_size,
        )
        tasks.append(discovery_sched.start())
        logger.info(
            "Neo: discovery scheduler started (every %dm, batch=%d)",
            settings.discovery_interval_minutes,
            settings.discovery_batch_size,
        )
    else:
        logger.info("Neo: discovery disabled (set NEO_DISCOVERY_ENABLED=true)")

    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


mcp = FastMCP(
    "neo",
    lifespan=_lifespan,
    instructions="""\
Neo is semantic memory for agent knowledge: a persistent typed knowledge graph where you store,
connect, and retrieve durable concepts, findings, theories, syntheses, and open research questions.

If the user asks what Neo is, whether you can use Neo, how to use Neo, or asks about semantic
memory, durable knowledge, research memory, knowledge graphs, or sparks, call get_neo_guidance
or get_agent_info first. Do not say you lack Neo knowledge before checking these tools.

Neo also runs an autonomous research pipeline that finds and ingests content on your behalf.

AFTER EVERY NEO TOOL CALL: Read the returned data, synthesize it, and present findings or next
steps to the user in natural language. Never leave a tool result dangling without commentary.

── RESEARCH DIRECTION ───────────────────────────────────────────────────────────────────────────
When a user tells you what topics they want you to follow or research:
  → call configure_agent(specialty="...", domains=[...])

That's it. Discovery starts automatically. No source configuration needed.
Example: user says "research leadership philosophy and entrepreneurship podcasts"
  → configure_agent(
       specialty="Research leadership philosophy and entrepreneurship. Focus on long-form
                  interviews and podcasts — shows like Diary of a CEO, Lex Fridman,
                  How I Built This. Prioritise durable principles and founder insights.",
       domains=["leadership", "entrepreneurship", "personal-productivity", "founder-mindset"]
     )
Neo will find relevant content, store it as nodes, and generate research questions automatically.
Use get_agent_info() to check the current research direction.

── KNOWLEDGE HIERARCHY ──────────────────────────────────────────────────────────────────────────
  Agents (root)
    └── {YourName} (your personal root — find it with get_node("{your_name}"))
          └── all your knowledge nodes

BEFORE STORING: call find_node_by_title first — if a match exists, call update_node instead.

── TYPICAL WORKFLOWS ────────────────────────────────────────────────────────────────────────────
Setting research direction:  user describes topics → configure_agent → confirm + describe pipeline
Researching a topic:         search_knowledge → find_node_by_title (dedup) → create_node → link_nodes
Checking what you know:      search_knowledge or get_node → summarize for user
Working the research queue:  get_sparks → investigate_spark, or manually investigate → resolve_spark/abandon_spark
Ingesting a specific video:  ingest_youtube(url, title, speaker, domain)

── NODE & EDGE TYPES ────────────────────────────────────────────────────────────────────────────
Node types: concept | finding | theory | synthesis  (never create: container, agent)
  concept   = a named knowledge thing — definition, category, mental model
  finding   = an observed fact or evidence-backed conclusion
  theory    = an explanatory claim about how/why something works
  synthesis = a conclusion produced by consolidating multiple nodes
Edge types: supports | contradicts | prerequisite_for | extends | example_of | questions | resolves | inspired | connects
""",
)


async def get_api():
    await init_db()
    api = get_api_singleton()
    await ensure_default_agent(api)
    return api


async def store_node_tool(**kwargs: Any) -> str:
    api = await get_api()
    agent = await ensure_default_agent(api)
    # Deduplication is enabled for all MCP calls: Atlas should update existing
    # nodes rather than create duplicates.
    result = await api.store_node(agent_id=agent["id"], deduplicate=True, **kwargs)
    return json.dumps(result, default=str)


async def get_node_tool(**kwargs: Any) -> str:
    api = await get_api()
    node_id = kwargs.get("node_id", "")
    # Auto-resolve: if it doesn't look like a UUID, treat it as a title search
    if node_id and not _UUID_RE.match(node_id):
        agent = await ensure_default_agent(api)
        found = await api.find_node_by_title(
            agent_id=agent["id"], title=node_id, exact=False, limit=5
        )
        match = found.get("selected_match")
        if match is None:
            return json.dumps(
                {"error": f"No node found matching title '{node_id}'", "suggestion": "Try search_knowledge for a semantic search."},
                default=str,
            )
        if found.get("ambiguous") and found.get("count", 0) > 1:
            # Return the best match but warn about ambiguity
            kwargs["node_id"] = match["id"]
            result = await api.get_node(**kwargs)
            result["_resolved_from_title"] = node_id
            result["_ambiguous"] = True
            result["_other_matches"] = [
                {"id": m["id"], "title": m["title"], "domain": m.get("domain")}
                for m in found.get("matches", [])[1:4]
            ]
            return json.dumps(result, default=str)
        kwargs["node_id"] = match["id"]
    result = await api.get_node(**kwargs)
    return json.dumps(result, default=str)


async def get_branch_tool(**kwargs: Any) -> str:
    api = await get_api()
    result = await api.get_branch(**kwargs)
    return json.dumps(result, default=str)


async def find_node_by_title_tool(**kwargs: Any) -> str:
    api = await get_api()
    agent = await ensure_default_agent(api)
    result = await api.find_node_by_title(agent_id=agent["id"], **kwargs)
    return json.dumps(result, default=str)


async def link_nodes_tool(**kwargs: Any) -> str:
    api = await get_api()
    agent = await ensure_default_agent(api)
    result = await api.link_nodes(agent_id=agent["id"], **kwargs)
    return json.dumps(result, default=str)


async def update_node_tool(**kwargs: Any) -> str:
    api = await get_api()
    result = await api.update_node(**kwargs)
    return json.dumps(result, default=str)


async def search_knowledge_tool(**kwargs: Any) -> str:
    api = await get_api()
    agent = await ensure_default_agent(api)
    result = await api.search_knowledge(agent_id=agent["id"], **kwargs)
    return json.dumps(result, default=str)


async def get_sparks_tool(**kwargs: Any) -> str:
    api = await get_api()
    agent = await ensure_default_agent(api)
    result = await api.get_sparks(agent_id=agent["id"], **kwargs)
    return json.dumps(result, default=str)


async def resolve_spark_tool(**kwargs: Any) -> str:
    api = await get_api()
    result = await api.resolve_spark(**kwargs)
    return json.dumps(result, default=str)


async def abandon_spark_tool(**kwargs: Any) -> str:
    api = await get_api()
    result = await api.abandon_spark(**kwargs)
    return json.dumps(result, default=str)


async def _build_resolver(api):
    from neo.core.resolver import ResolutionLLM, SparkResolver
    from neo.core.web_search import WebSearchClient, NullWebSearch

    res_key = settings.llm_resolution_api_key or settings.llm_spark_api_key
    if not res_key:
        raise RuntimeError("Spark investigation requires NEO_LLM_RESOLUTION_API_KEY or NEO_LLM_SPARK_API_KEY")
    llm = ResolutionLLM(
        api_key=res_key,
        model=settings.llm_resolution_model or settings.llm_spark_model,
        base_url=settings.llm_resolution_base_url or settings.llm_spark_base_url,
    )
    web_search = (
        WebSearchClient(settings.search_provider, settings.search_api_key)
        if settings.search_api_key
        else NullWebSearch()
    )
    return SparkResolver(api, llm, web_search)


async def investigate_spark_tool(**kwargs: Any) -> str:
    api = await get_api()
    agent = await ensure_default_agent(api)
    spark_id = kwargs["spark_id"]
    mode = kwargs.get("mode", "apply")

    spark = None
    for status in ("active", "resolved", "abandoned"):
        sparks = await api.store.get_sparks(agent["id"], status=status, limit=500)
        spark = next((s for s in sparks if s["id"] == spark_id), None)
        if spark is not None:
            break
    if spark is None:
        return json.dumps({"error": f"Spark {spark_id} not found"})
    if spark.get("status") != "active" and mode == "apply":
        return json.dumps({"error": f"Spark {spark_id} is already {spark.get('status')}"})

    if spark.get("target_node_id"):
        target = await api.store.get_node(spark["target_node_id"])
        if target:
            spark = {
                **spark,
                "target_title": target.get("title", ""),
                "target_content": target.get("content", ""),
                "target_summary": target.get("summary", ""),
                "node_domain": target.get("domain"),
            }

    resolver = await _build_resolver(api)
    result = await resolver.resolve(spark, agent, mode=mode, trigger="manual")
    return json.dumps(result, default=str)


async def get_activity_summary_tool(**kwargs: Any) -> str:
    api = await get_api()
    agent = await ensure_default_agent(api)
    result = await api.get_activity_summary(agent_id=agent["id"], **kwargs)
    return json.dumps(result, default=str)


@mcp.tool()
async def get_neo_guidance() -> str:
    """Use this first when the user asks what Neo is, whether you can use Neo, how to use Neo, or how to use semantic memory.

    Neo is the MCP semantic memory server for durable agent knowledge. This tool explains the available workflows,
    when to search Neo, when to store knowledge, how sparks work, and which Neo tools to call next.

    After calling: tell the user briefly that Neo is available and summarize the relevant workflow."""
    return json.dumps(
        {
            "what_neo_is": (
                "Neo is semantic memory for AI agents: a persistent typed knowledge graph "
                "for durable concepts, findings, theories, syntheses, relationships, and open research questions."
            ),
            "when_to_use_neo": [
                "Before answering knowledge-heavy or research-heavy questions, call search_knowledge.",
                "When the user asks you to remember durable knowledge or research findings, store or update a node.",
                "When the user asks what you know, inspect Neo with search_knowledge, get_agent_info, or get_activity_summary.",
                "When the user asks what to investigate next, call get_sparks.",
                "When the user sets a research direction, call configure_agent.",
            ],
            "common_workflows": {
                "orientation": ["get_neo_guidance", "get_agent_info", "get_activity_summary"],
                "answer_from_memory": ["search_knowledge", "get_node if more detail is needed", "summarize for the user"],
                "store_knowledge": ["find_node_by_title", "update_node if found, otherwise create_node", "link_nodes when relationships are clear"],
                "research_queue": ["get_sparks", "investigate_spark", "or manually investigate then resolve_spark/abandon_spark"],
                "research_direction": ["configure_agent", "trigger_discovery if immediate ingestion is useful"],
            },
            "node_types": {
                "concept": "Named knowledge thing, definition, category, or mental model.",
                "finding": "Observed fact or evidence-backed conclusion.",
                "theory": "Explanatory claim about how or why something works.",
                "synthesis": "Conclusion produced by consolidating multiple nodes.",
            },
            "edge_types": [
                "supports",
                "contradicts",
                "prerequisite_for",
                "extends",
                "example_of",
                "questions",
                "resolves",
                "inspired",
                "connects",
            ],
            "important_rules": [
                "Before storing, call find_node_by_title to avoid duplicates.",
                "Use update_node instead of create_node when a matching node already exists.",
                "When parent_id is omitted, Neo stores new knowledge under the agent root.",
                "Use investigate_spark for the standard spark research/debate/judge pipeline.",
                "After every Neo tool call, synthesize the returned data in natural language for the user.",
            ],
        },
        indent=2,
    )


@mcp.tool()
async def create_node(node_type: str, title: str, content: str, summary: str | None = None, confidence: float = 0.5, parent_id: str | None = None, source_id: str | None = None, spark_id: str | None = None, domain: str | None = None, metadata: dict[str, Any] | None = None) -> str:
    """Use this when the user asks you to remember durable knowledge, research findings, concepts, theories, or syntheses in Neo.

    Create a new knowledge node in Neo. Auto-generates embedding and queues spark generation.

    node_type: concept | finding | theory | synthesis  (never use container or agent — those are reserved)
    confidence: 0.0-1.0 (how certain you are)
    parent_id: optional UUID of the most relevant knowledge parent.
               If omitted, Neo stores the node under this agent's root node.
    domain: topic area (e.g. "machine-learning", "agents")
    metadata: optional structured context. For provenance, prefer {"url": "...", "source_title": "..."}.

    IMPORTANT: Call find_node_by_title(title) BEFORE calling this tool.
    If a node with the same title already exists, call update_node instead.
    Duplicate nodes fragment the graph and make search less reliable.

    If result contains "duplicate": true, the node already existed — do NOT store again.

    Returns the created node's ID. Present the stored node to the user and mention if sparks are pending."""
    return await store_node_tool(
        node_type=node_type,
        title=title,
        content=content,
        summary=summary,
        confidence=confidence,
        parent_id=parent_id,
        source_id=source_id,
        spark_id=spark_id,
        domain=domain,
        metadata=metadata,
    )


@mcp.tool()
async def store_node(node_type: str, title: str, content: str, summary: str | None = None, confidence: float = 0.5, parent_id: str | None = None, source_id: str | None = None, spark_id: str | None = None, domain: str | None = None, metadata: dict[str, Any] | None = None) -> str:
    """Compatibility alias for create_node. Prefer create_node for new usage."""
    return await create_node(
        node_type=node_type,
        title=title,
        content=content,
        summary=summary,
        confidence=confidence,
        parent_id=parent_id,
        source_id=source_id,
        spark_id=spark_id,
        domain=domain,
        metadata=metadata,
    )


@mcp.tool()
async def get_node(node_id: str, include_edges: bool = True, include_ancestors: bool = True, include_children: bool = False) -> str:
    """Retrieve a node by ID or title. Accepts either a UUID or a node title (auto-resolves via title search).

    include_edges: also return all edges connected to this node
    include_ancestors: also return the parent chain up to root
    include_children: also return direct children

    After calling: read the node's content, edges, and context, then summarize findings for the user."""
    return await get_node_tool(
        node_id=node_id,
        include_edges=include_edges,
        include_ancestors=include_ancestors,
        include_children=include_children,
    )


@mcp.tool()
async def get_branch(root_node_id: str, max_depth: int = 2, include_edges: bool = True) -> str:
    """Get a full branch of the knowledge tree starting from a root node. Returns the root plus all descendants up to max_depth.

    Useful for exploring an entire topic area or ontology subtree.

    After calling: present the branch structure to the user, noting how nodes are organized."""
    return await get_branch_tool(
        root_node_id=root_node_id,
        max_depth=max_depth,
        include_edges=include_edges,
    )


@mcp.tool()
async def find_node_by_title(title: str, exact: bool = True, domain: str | None = None, limit: int = 10) -> str:
    """Find nodes by title. Use exact=False for substring matching.

    Tip: get_node also accepts titles directly — use this tool when you specifically need to search for multiple matches or check for ambiguity.

    After calling: if ambiguous, clarify which node with the user. If found, present the match."""
    return await find_node_by_title_tool(
        title=title,
        exact=exact,
        domain=domain,
        limit=limit,
    )


@mcp.tool()
async def link_nodes(from_node_id: str, to_node_id: str, edge_type: str, description: str, weight: float = 0.5, source_id: str | None = None) -> str:
    """Create a typed edge between two nodes. Requires node UUIDs (use find_node_by_title first if needed).

    edge_type: supports | contradicts | prerequisite_for | extends | example_of | questions | resolves | inspired | connects
    weight: 0.0-1.0 (strength of relationship)
    description: brief description of why this relationship exists

    If edge_type is 'contradicts', a contradiction spark is auto-generated.

    After calling: confirm the link was created and note any auto-generated sparks."""
    return await link_nodes_tool(
        from_node_id=from_node_id,
        to_node_id=to_node_id,
        edge_type=edge_type,
        description=description,
        weight=weight,
        source_id=source_id,
    )


@mcp.tool()
async def update_node(node_id: str, content: str | None = None, summary: str | None = None, confidence: float | None = None, parent_id: str | None = None, metadata: dict[str, Any] | None = None) -> str:
    """Update a node's content, summary, confidence, parent, or metadata. Re-embeds automatically if content changes.

    Only pass the fields you want to change — omit the rest.
    parent_id moves the node under a different knowledge parent. Neo validates that
    the parent exists and rejects self-parenting or cycles.
    metadata is merged into existing metadata; use it for provenance such as source URL and title.

    After calling: confirm what was updated and present the new state."""
    return await update_node_tool(
        node_id=node_id,
        content=content,
        summary=summary,
        confidence=confidence,
        parent_id=parent_id,
        metadata=metadata,
    )


@mcp.tool()
async def search_knowledge(query: str, top_k: int = 10, hop_depth: int = 2, min_weight: float = 0.5, token_budget: int = 2000, node_type: str | None = None, domain: str | None = None, scope: str = "self") -> str:
    """Use this before answering research-heavy or knowledge-heavy questions to retrieve durable knowledge from Neo.

    Semantic search across all knowledge. Embeds your query, finds similar nodes, expands via graph traversal, and returns ranked results within token budget.

    query: natural language question or topic
    top_k: max seed nodes from vector search
    hop_depth: how many edge hops to expand (1-3)
    token_budget: max tokens in response
    scope: "self" (own nodes only, default) | "network" (all agents in the installation)

    After calling: synthesize the returned nodes into a coherent answer. Note contradictions and gaps. Mention relevant sparks."""
    return await search_knowledge_tool(
        query=query,
        top_k=top_k,
        hop_depth=hop_depth,
        min_weight=min_weight,
        token_budget=token_budget,
        node_type=node_type,
        domain=domain,
        scope=scope,
    )


@mcp.tool()
async def get_sparks(status: str = "active", spark_type: str | None = None, domain: str | None = None, min_priority: float | None = None, limit: int = 3) -> str:
    """Get the research agenda — prioritized sparks (gaps, questions, contradictions) that need attention.

    spark_type: open_question | contradiction | weak_edge | isolated_node | thin_domain
    status: active | resolved | abandoned
    limit: default 3 — work sparks in small batches to stay within context. Call again after resolving to get the next batch.

    Preferred protocol:
    Call investigate_spark(spark_id, mode="apply"). Neo will run the same
    role-isolated research/debate/judge pipeline used by the background resolver.

    Manual fallback:
    1. Read the target node and its immediate neighbours (get_node with include_edges=true).
    2. Form a conclusion. If debating with a sub-agent, limit to 2 turns each; extract the answer, discard the dialogue.
    3. Store exactly one node with the settled insight (or update an existing node if one already covers it).
    4. Call resolve_spark with that node's ID, OR call abandon_spark if the tension was a false positive.

    RULE: Every spark you touch must end with resolve_spark or abandon_spark before you move to the next one.
    Never leave a spark open after investigating it. A spark is only closed when one of those two tools is called."""
    return await get_sparks_tool(status=status, spark_type=spark_type, domain=domain, min_priority=min_priority, limit=limit)


@mcp.tool()
async def investigate_spark(spark_id: str, mode: str = "apply") -> str:
    """Run Neo's standard spark investigation pipeline.

    spark_id: UUID of the spark to investigate
    mode: "apply" mutates the graph and closes the spark; "preview" returns
          candidates and blind judge votes without mutation.

    The same pipeline is used for manual and background resolution:
    context collection → internal/web research → Candidate A → Candidate B →
    Candidate AB synthesis → blind judge panel → winning graph action.

    Outcomes can create a node, update the target node, resolve with no graph
    change, or abandon a false-positive spark. Resolved sparks disappear from
    the active visualizer queue; nodes that absorb sparks turn progressively
    gold and physically stronger."""
    if mode not in {"apply", "preview"}:
        return json.dumps({"error": "mode must be 'apply' or 'preview'"})
    return await investigate_spark_tool(spark_id=spark_id, mode=mode)


@mcp.tool()
async def resolve_spark(spark_id: str, notes: str | None = None, node_ids: str | None = None) -> str:
    """Mark a spark as resolved.

    spark_id: UUID of the spark to resolve
    notes: one-sentence summary of what was concluded
    node_ids: JSON array string of node UUIDs that addressed this spark, e.g. '["uuid1","uuid2"]'
              Pass the node that contains the settled insight. Omit only if the conclusion was
              "this was already covered" and no node was created or updated.

    Use resolve_spark when the tension was real and you reached a conclusion.
    Use abandon_spark instead when the tension was a false positive (the contradiction doesn't actually exist).

    The node passed in node_ids should contain the conclusion, not the investigation.
    Store the answer; discard the process.

    After calling: the spark disappears from the active queue. Confirm it was resolved."""
    import json as _json
    parsed_ids: list[str] | None = None
    if node_ids:
        try:
            parsed_ids = _json.loads(node_ids)
        except Exception:
            parsed_ids = [node_ids]  # treat as a single bare UUID
    return await resolve_spark_tool(spark_id=spark_id, node_ids=parsed_ids, notes=notes)


@mcp.tool()
async def delete_node(node_id: str) -> str:
    """Permanently delete a node and all its edges from the graph.

    node_id: UUID or title of the node to delete

    What cascades automatically:
    - All edges connected to this node (both directions)
    - Child nodes' parent_id becomes NULL (they become root nodes, not deleted)
    - Sparks that targeted this node lose their target reference (not deleted)

    Use this for duplicate nodes, test nodes, or nodes that were stored in error.
    Prefer update_node if the content just needs correction.

    After calling: confirm deletion and note any orphaned children or sparks."""
    api = await get_api()
    # Support title lookup same as get_node
    if not _UUID_RE.match(node_id):
        agent = await ensure_default_agent(api)
        result = await api.find_node_by_title(agent_id=agent["id"], title=node_id, exact=False, limit=1)
        match = result.get("selected_match") or (result.get("matches") or [None])[0]
        if match is None:
            return json.dumps({"error": f"No node found with title '{node_id}'"})
        node_id = match["id"]
    result = await api.delete_node(node_id=node_id)
    return json.dumps(result, default=str)


@mcp.tool()
async def abandon_spark(spark_id: str, reason: str) -> str:
    """Dismiss a spark as a false positive — it describes a contradiction or gap that doesn't actually exist.

    spark_id: UUID of the spark to abandon
    reason: why this spark was a false positive (required — helps tune future spark generation)

    Use this when the spark misread the node content and the apparent tension isn't real.
    Use resolve_spark instead when the tension was real but you've reached a conclusion.

    After calling: confirm the spark was abandoned and note what the misread was."""
    return await abandon_spark_tool(spark_id=spark_id, reason=reason)


@mcp.tool()
async def get_activity_summary(since: str | None = None) -> str:
    """Get a structured summary of Neo activity since a given time (ISO timestamp, defaults to last 24h).

    Returns: node/edge/spark counts, recent nodes, active sparks, contradictions, active domains.

    After calling: present the activity summary as a brief status report to the user."""
    return await get_activity_summary_tool(since=since)


@mcp.tool()
async def configure_agent(
    specialty: str | None = None,
    domains: list[str] | None = None,
    skill_notes: str | None = None,
    suggested_sources: list[str] | None = None,
) -> str:
    """Set what this agent researches. This activates the entire research pipeline.

    Call this whenever a user tells you what topics they want you to follow.
    You don't need to configure sources or schedule jobs — just set the specialty
    and everything else runs automatically.

    specialty: 2-4 sentences describing the research focus in plain language.
               Be specific about content type (interviews, podcasts, talks) and
               any named shows or thinkers the user mentioned.
               Example: "Research personal productivity, entrepreneurship, and leadership
               philosophy. Primary sources are long-form interviews and podcasts —
               shows like Diary of a CEO, Lex Fridman, How I Built This, and
               similar. Prioritise durable principles and founder insights over
               tactical how-to content."

    domains: 4-8 short domain tags for priority scoring.
             Example: ["personal-productivity", "entrepreneurship", "leadership",
                       "founder-mindset", "philosophy", "mental-models"]

    skill_notes: optional constraints or preferences.
                 Example: "Prefer practitioners over academics. Note speaker and
                 show when storing findings — provenance matters."

    suggested_sources: optional source hints to guide autonomous discovery.
                       These live on the agent config/root context, not as graph nodes.
                       Example: ["Diary of a CEO", "Lex Fridman", "How I Built This"]

    What happens immediately after calling:
    - Research direction is saved to the agent record and its root graph node
    - A discovery run fires in the background — Neo starts finding and ingesting
      relevant content right away (first results within ~60 seconds)
    - All background jobs (contemplation, resolution) inherit the new focus

    After calling: confirm what research direction is set, tell the user discovery
    is running, and offer to refine the focus if needed."""
    api = await get_api()
    agent = await ensure_default_agent(api)
    result = await api.configure_agent(
        agent_id=agent["id"],
        specialty=specialty,
        domains=domains,
        skill_notes=skill_notes,
        suggested_sources=suggested_sources,
    )
    return json.dumps(result, default=str)


@mcp.tool()
async def get_agent_info() -> str:
    """Use this to check the current Neo semantic memory configuration for this agent.

    Get this agent's current configuration: name, research direction, domains, skill notes.

    After calling: summarize the agent's research focus for the user."""
    api = await get_api()
    agent = await ensure_default_agent(api)
    result = await api.get_agent_info(agent_id=agent["id"])
    return json.dumps(result, default=str)


@mcp.tool()
async def trigger_discovery() -> str:
    """Run the discovery job immediately — don't wait for the next scheduled cycle.

    Useful after configure_agent, or for triggering a manual research pass when
    you want Neo to look for new content matching the current research direction.

    After calling: report how many new items were ingested."""
    api = await get_api()
    agent = await ensure_default_agent(api)
    from neo.core.discovery import DiscoveryJob
    from neo.core.youtube import YouTubeSearchClient, EchoSearchAsYouTube
    from neo.core.web_search import WebSearchClient

    yt_search = None
    if settings.youtube_api_key:
        yt_search = YouTubeSearchClient(settings.youtube_api_key)
    elif settings.search_api_key:
        yt_search = EchoSearchAsYouTube(
            WebSearchClient(settings.search_provider, settings.search_api_key)
        )

    res_key   = settings.llm_resolution_api_key or settings.llm_spark_api_key
    res_model = settings.llm_resolution_model or settings.llm_spark_model
    res_url   = settings.llm_resolution_base_url or settings.llm_spark_base_url
    discovery_llm = None
    if res_key:
        from neo.core.resolver import ResolutionLLM
        discovery_llm = ResolutionLLM(api_key=res_key, model=res_model, base_url=res_url)

    job = DiscoveryJob(api, llm=discovery_llm, yt_search=yt_search)
    fresh_agent = await api.store.get_agent(agent["id"])
    result = await job.run(
        fresh_agent,
        batch_size=settings.discovery_batch_size,
        lookback_days=settings.discovery_lookback_days,
    )
    return json.dumps(result, default=str)


@mcp.tool()
async def ingest_youtube(
    url: str,
    title: str | None = None,
    speaker: str | None = None,
    domain: str | None = None,
    parent_id: str | None = None,
    query_focus: str | None = None,
) -> str:
    """Fetch a YouTube video transcript and store key insights as nodes in Neo.

    Use this to ingest podcasts, interviews, talks, and lectures — especially
    long-form content like Diary of a CEO, Lex Fridman, How I Built This, etc.

    url: full YouTube URL (youtube.com/watch?v=... or youtu.be/...)
    title: human-readable title, e.g. "Steven Bartlett — Why Most People Never Achieve Their Goals"
           If omitted, a title is inferred from the URL.
    speaker: primary speaker or guest, e.g. "Steven Bartlett" or "James Clear"
             Stored in node metadata for provenance.
    domain: topic tag, e.g. "entrepreneurship" or "personal-productivity"
    parent_id: parent node UUID — set to the most relevant topic node or your root node
    query_focus: optional — if you only want insights relevant to a specific question,
                 e.g. "how to build a morning routine". Filters the transcript excerpt.
                 If omitted, a broad summary of the full transcript is extracted.

    What this does:
    1. Fetches the auto-generated transcript (no API key required)
    2. Extracts distinct durable learnings from the transcript or focused excerpt
    3. Stores each learning as its own finding node
    4. Returns the node IDs so you can link them to related concepts

    After calling: present the stored node titles and key insights to the user.
    Then consider linking them to related concept nodes and checking for new sparks."""
    import asyncio as _asyncio

    try:
        from neo.core.youtube import get_fetcher, extract_video_id, is_youtube_url
    except ImportError:
        return json.dumps({"error": "youtube-transcript-api not installed. Run: pip install 'neo-agent-knowledge[sparks]'"})

    if not is_youtube_url(url):
        return json.dumps({"error": f"Not a recognised YouTube URL: {url}"})

    api = await get_api()
    agent = await ensure_default_agent(api)

    # Fetch transcript in a thread (sync library)
    try:
        fetcher = get_fetcher()
        loop = _asyncio.get_event_loop()
        if query_focus:
            data = await loop.run_in_executor(
                None, lambda: fetcher.fetch_relevant_excerpt(url, query_focus, max_chars=3000)
            )
            excerpt = data["excerpt"]
        else:
            data = await loop.run_in_executor(None, lambda: fetcher.fetch_url(url))
            excerpt = data["text"]

    except Exception as exc:
        return json.dumps({"error": f"Transcript fetch failed: {exc}"})

    vid = extract_video_id(url)
    duration_mins = round(data.get("duration_seconds", 0) / 60, 1) if data.get("duration_seconds") else None
    inferred_title = title or f"YouTube: {vid}"

    provenance_parts = [f"Source: {url}"]
    if speaker:
        provenance_parts.append(f"Speaker: {speaker}")
    if duration_mins:
        provenance_parts.append(f"Duration: ~{duration_mins} min")
    if query_focus:
        provenance_parts.append(f"Query focus: {query_focus}")
    from neo.core.discovery import append_source_provenance, extract_knowledge_findings

    res_key   = settings.llm_resolution_api_key or settings.llm_spark_api_key
    res_model = settings.llm_resolution_model or settings.llm_spark_model
    res_url   = settings.llm_resolution_base_url or settings.llm_spark_base_url
    discovery_llm = None
    if res_key:
        from neo.core.resolver import ResolutionLLM
        discovery_llm = ResolutionLLM(api_key=res_key, model=res_model, base_url=res_url)

    findings = await extract_knowledge_findings(
        source_title=inferred_title,
        source_text=excerpt,
        source_type="youtube",
        source_url=url,
        agent_focus=query_focus or (agent.get("specialty") or ""),
        llm=discovery_llm,
        max_findings=5,
        confidence=0.7,
    )
    if not findings:
        return json.dumps({"error": "No durable findings could be extracted from the transcript."})

    results: list[dict[str, Any]] = []
    for index, finding in enumerate(findings, start=1):
        metadata: dict = {
            "source_type": "youtube",
            "video_id": vid,
            "source_title": inferred_title,
            "url": url,
            "finding_index": index,
            "findings_total": len(findings),
        }
        if speaker:
            metadata["speaker"] = speaker
        if query_focus:
            metadata["query_focus"] = query_focus

        result = await api.store_node(
            agent_id=agent["id"],
            node_type="finding",
            title=finding["title"],
            content=append_source_provenance(finding["content"], provenance_parts),
            summary=finding["summary"],
            confidence=finding["confidence"],
            parent_id=parent_id,
            domain=domain,
            metadata=metadata,
            generate_sparks=True,
            deduplicate=True,
        )
        results.append(result)

    return json.dumps(
        {
            "nodes_created": len([node for node in results if not node.get("duplicate")]),
            "nodes": results,
            "source_title": inferred_title,
            "transcript_chars": len(data["text"]),
            "excerpt_chars": len(excerpt),
        },
        default=str,
    )


@mcp.prompt()
def neo_usage_guidance() -> str:
    """How to use Neo — inject this into your system prompt for best results."""
    return """\
## Neo — Semantic Knowledge Graph

Neo is your persistent knowledge graph. Use it to store, connect, and retrieve what you learn.

### Knowledge hierarchy
Every agent has a fixed root structure that must be maintained:
  Agents (root concept node)
    └── {YourName} (your personal root — find its ID with get_node("{your_name}"))
          └── your knowledge nodes

If parent_id is omitted, Neo stores new knowledge under the agent root.
Child nodes should reference their logical parent's ID when you know it.

### Rules
**Before storing:** always call `find_node_by_title(title)` first.
  - If a match exists → call `update_node` instead of `create_node`.
  - Never create two nodes with the same title. Duplicates fragment the graph.
**Before answering knowledge-heavy questions:** call `search_knowledge` first.
**get_node accepts a title OR a UUID** — you don't need the ID.
**After every Neo tool call:** synthesize the result in plain language.

Node types: concept · finding · theory · synthesis  (container and agent are reserved — never store manually)
Edge types: supports · contradicts · prerequisite_for · extends · example_of · questions · resolves · inspired · connects
"""


if __name__ == "__main__":
    mcp.run()
