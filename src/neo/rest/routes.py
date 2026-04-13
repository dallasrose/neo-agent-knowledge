from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from neo.config import settings
from neo.core.api import NeoAPI
from neo.core.consolidation import ConsolidationEngine
from neo.rest.schemas import (
    ConfigureAgentRequest,
    HealthResponse,
    LinkNodesRequest,
    MoveNodeRequest,
    ResolveSparkRequest,
    SearchKnowledgeRequest,
    StoreNodeRequest,
    UpdateNodeRequest,
)
from neo.runtime import ensure_default_agent

router = APIRouter(prefix="/api")


def get_api(request: Request) -> NeoAPI:
    api: NeoAPI | None = getattr(request.app.state, "neo_api", None)
    if api is None:
        raise HTTPException(status_code=503, detail="Neo API unavailable")
    return api


def compact_node(node: dict) -> dict:
    return {
        "id": node["id"],
        "node_type": node["node_type"],
        "title": node["title"],
        "summary": node["summary"],
        "confidence": node["confidence"],
        "domain": node.get("domain"),
        "parent_id": node.get("parent_id"),
        "source_id": node.get("source_id"),
        "spark_id": node.get("spark_id"),
        "created_at": node["created_at"],
        "updated_at": node["updated_at"],
        "consolidation_version": node["consolidation_version"],
    }


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    db_scheme = urlparse(settings.db_connection_uri).scheme or settings.db_connection_uri.split(":", 1)[0]
    return HealthResponse(
        status="ok",
        agent_name=settings.agent_name,
        db_scheme=db_scheme,
        consolidation_enabled=settings.consolidation_enabled,
        embedding_provider=settings.embedding_provider,
        embedding_fallback_enabled=settings.embedding_fallback_enabled,
    )


@router.post("/nodes")
async def store_node(payload: StoreNodeRequest, api: NeoAPI = Depends(get_api)) -> dict:
    agent = await ensure_default_agent(api)
    return await api.store_node(agent_id=agent["id"], **payload.model_dump())


@router.get("/nodes")
async def list_nodes(
    request: Request,
    node_type: str | None = Query(default=None),
    domain: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    api: NeoAPI = Depends(get_api),
) -> list[dict]:
    agent = await ensure_default_agent(api)
    nodes = await api.store.get_nodes_by_agent(
        agent["id"],
        node_type=node_type,
        domain=domain,
        limit=limit,
        offset=offset,
    )
    return [compact_node(node) for node in nodes]


@router.post("/edges")
async def link_nodes(payload: LinkNodesRequest, api: NeoAPI = Depends(get_api)) -> dict:
    agent = await ensure_default_agent(api)
    return await api.link_nodes(agent_id=agent["id"], **payload.model_dump())


@router.patch("/nodes/{node_id}")
async def update_node(node_id: str, payload: UpdateNodeRequest, api: NeoAPI = Depends(get_api)) -> dict:
    return await api.update_node(node_id=node_id, **payload.model_dump(exclude_none=True))


@router.delete("/nodes/{node_id}")
async def delete_node(node_id: str, api: NeoAPI = Depends(get_api)) -> dict:
    return await api.delete_node(node_id=node_id)


@router.patch("/nodes/{node_id}/parent")
async def move_node(node_id: str, payload: MoveNodeRequest, api: NeoAPI = Depends(get_api)) -> dict:
    if payload.parent_id is None:
        return await api.store.update_node(node_id, parent_id=None)
    return await api.update_node(node_id=node_id, parent_id=payload.parent_id)


@router.post("/search")
async def search_knowledge(payload: SearchKnowledgeRequest, api: NeoAPI = Depends(get_api)) -> dict:
    agent = await ensure_default_agent(api)
    return await api.search_knowledge(agent_id=agent["id"], **payload.model_dump())


@router.get("/sparks")
async def get_sparks(
    status: str = Query(default="active"),
    spark_type: str | None = Query(default=None),
    domain: str | None = Query(default=None),
    min_priority: float | None = Query(default=None, ge=0.0, le=1.0),
    limit: int = Query(default=5, ge=1, le=100),
    api: NeoAPI = Depends(get_api),
) -> list[dict]:
    agent = await ensure_default_agent(api)
    return await api.get_sparks(
        agent_id=agent["id"],
        status=status,
        spark_type=spark_type,
        domain=domain,
        min_priority=min_priority,
        limit=limit,
    )


@router.post("/sparks/{spark_id}/resolve")
async def resolve_spark(spark_id: str, payload: ResolveSparkRequest, api: NeoAPI = Depends(get_api)) -> dict:
    return await api.resolve_spark(spark_id=spark_id, **payload.model_dump())


@router.get("/activity")
async def get_activity_summary(since: str | None = None, api: NeoAPI = Depends(get_api)) -> dict:
    agent = await ensure_default_agent(api)
    return await api.get_activity_summary(agent_id=agent["id"], since=since)


@router.get("/nodes/by-title")
async def find_node_by_title(
    title: str = Query(min_length=1),
    exact: bool = Query(default=True),
    domain: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
    api: NeoAPI = Depends(get_api),
) -> dict:
    agent = await ensure_default_agent(api)
    return await api.find_node_by_title(
        agent_id=agent["id"],
        title=title,
        exact=exact,
        domain=domain,
        limit=limit,
    )


@router.get("/nodes/{node_id}")
async def get_node(node_id: str, api: NeoAPI = Depends(get_api)) -> dict:
    return await api.get_node(node_id=node_id)


@router.get("/nodes/{node_id}/branch")
async def get_branch(node_id: str, max_depth: int = Query(default=2, ge=0, le=8), api: NeoAPI = Depends(get_api)) -> dict:
    return await api.get_branch(root_node_id=node_id, max_depth=max_depth)


@router.get("/graph")
async def get_graph(
    limit: int = Query(default=500, ge=1, le=2000),
    api: NeoAPI = Depends(get_api),
) -> dict:
    """Return all nodes, edges, and active sparks for graph rendering."""
    agent = await ensure_default_agent(api)
    nodes = await api.store.get_nodes_by_agent(agent["id"], limit=limit)
    edges = await api.store.get_all_edges(agent["id"], limit=limit * 4)
    active_sparks = await api.store.get_sparks(agent["id"], status="active", limit=200)
    resolved_sparks = await api.store.get_sparks(agent["id"], status="resolved", limit=500)
    # Count how many resolved sparks each node has absorbed. Active sparks render
    # as spark nodes; resolved sparks disappear and strengthen/tint their nodes.
    spark_node_counts: dict[str, int] = {}
    for s in resolved_sparks:
        node_ids = (s.get("metadata") or {}).get("resolved_node_ids") or []
        if not node_ids and s.get("resolved_node_id"):
            node_ids = [s["resolved_node_id"]]
        for nid in node_ids:
            spark_node_counts[nid] = spark_node_counts.get(nid, 0) + 1
    return {
        "nodes": [compact_node(n) for n in nodes],
        "edges": edges,
        "sparks": active_sparks,
        "spark_node_counts": spark_node_counts,
    }


@router.get("/agent")
async def get_agent_info(api: NeoAPI = Depends(get_api)) -> dict:
    """Return current agent configuration: specialty, domains, skill_notes."""
    agent = await ensure_default_agent(api)
    return await api.get_agent_info(agent_id=agent["id"])


@router.patch("/agent")
async def configure_agent(payload: ConfigureAgentRequest, api: NeoAPI = Depends(get_api)) -> dict:
    """Set the agent's research direction. Immediately visible to background jobs."""
    agent = await ensure_default_agent(api)
    return await api.configure_agent(
        agent_id=agent["id"],
        **payload.model_dump(exclude_none=True),
    )


@router.post("/consolidate")
async def consolidate(api: NeoAPI = Depends(get_api)) -> dict:
    agent = await ensure_default_agent(api)
    engine = ConsolidationEngine(api.store)
    return await engine.run(agent["id"])


@router.get("/sources")
async def list_sources(api: NeoAPI = Depends(get_api)) -> dict:
    """List configured research sources."""
    agent = await ensure_default_agent(api)
    config = agent.get("config") or {}
    sources = config.get("research_sources") or []
    return {"sources": sources, "count": len(sources)}


@router.post("/discovery/trigger")
async def trigger_discovery(api: NeoAPI = Depends(get_api)) -> dict:
    """Trigger a discovery run immediately."""
    from neo.core.discovery import DiscoveryJob
    agent = await ensure_default_agent(api)
    fresh_agent = await api.store.get_agent(agent["id"])
    job = DiscoveryJob(api)
    return await job.run(fresh_agent, batch_size=settings.discovery_batch_size)
