from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from neo.core.assembler import WorkingMemoryAssembler
from neo.core.relationships import HeuristicRelationshipJudge, RelationshipJudge
from neo.core.sparks import SparkGenerator
from neo.embedding.client import EmbeddingClient
from neo.enums import EdgeType, NodeType
from neo.store.interface import StoreInterface


class NeoAPI:
    AUTO_LINK_MIN_SIMILARITY = 0.82
    AUTO_LINK_MAX_EDGES = 3

    def __init__(
        self,
        store: StoreInterface,
        *,
        embedding_client: EmbeddingClient | Any,
        assembler: WorkingMemoryAssembler | None = None,
        spark_generator: SparkGenerator | None = None,
        relationship_judge: RelationshipJudge | None = None,
    ) -> None:
        self.store = store
        self.embedding_client = embedding_client
        self.assembler = assembler or WorkingMemoryAssembler(store)
        self.spark_generator = spark_generator or SparkGenerator(store)
        self.relationship_judge = relationship_judge or HeuristicRelationshipJudge()
        # Keep strong references to fire-and-forget tasks so GC can't collect them mid-run
        self._background_tasks: set[asyncio.Task] = set()

    async def store_node(
        self,
        *,
        agent_id: str,
        node_type: str,
        title: str,
        content: str,
        summary: str | None = None,
        confidence: float = 0.5,
        parent_id: str | None = None,
        source_id: str | None = None,
        spark_id: str | None = None,
        domain: str | None = None,
        metadata: dict[str, Any] | None = None,
        generate_sparks: bool = True,
        deduplicate: bool = False,
    ) -> dict[str, Any]:
        self._validate_node_type(node_type)
        if parent_id is None:
            parent_id = await self._default_parent_id(agent_id)
        else:
            await self._validate_parent_id(agent_id=agent_id, parent_id=parent_id)

        # Deduplication: if a node with the same title and type already exists,
        # return it instead of creating a duplicate.
        if deduplicate:
            existing = await self._find_duplicate(agent_id, title, node_type)
            if existing is not None:
                return {
                    "id": existing["id"],
                    "title": existing["title"],
                    "node_type": existing["node_type"],
                    "confidence": existing["confidence"],
                    "spark_generation": "skipped",
                    "duplicate": True,
                    "message": (
                        f"Node '{title}' already exists (id={existing['id']}). "
                        "Use update_node to revise its content."
                    ),
                }

        embedding = await self.embedding_client.embed_text(title, content)
        node = await self.store.create_node(
            agent_id,
            node_type,
            title,
            content,
            summary=summary or self._summarize(content),
            confidence=confidence,
            parent_id=parent_id,
            source_id=source_id,
            spark_id=spark_id,
            embedding=embedding,
            domain=domain,
            metadata=metadata,
        )
        if generate_sparks:
            agent = await self.store.get_agent(agent_id)
            if agent is not None:
                task = asyncio.create_task(
                    self.spark_generator.generate_for_node(
                        agent=agent,
                        node=node,
                        max_sparks_per_node=agent.get("config", {}).get("max_sparks_per_node", 3),
                        max_sparks_per_day=agent.get("config", {}).get("max_sparks_per_day", 20),
                    )
                )
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
        linked_edges = await self._link_related_nodes(agent_id, node, embedding)
        return {
            "id": node["id"],
            "title": node["title"],
            "node_type": node["node_type"],
            "confidence": node["confidence"],
            "spark_generation": "triggered" if generate_sparks else "skipped",
            "edges_created": len(linked_edges),
        }

    async def get_node(
        self,
        *,
        node_id: str,
        include_edges: bool = True,
        include_ancestors: bool = True,
        include_children: bool = False,
    ) -> dict[str, Any]:
        node = await self.store.get_node(node_id)
        if node is None:
            raise ValueError(f"Node {node_id} not found")

        result: dict[str, Any] = {"node": node}
        if include_edges:
            result["edges"] = await self.store.get_edges(node_id)
        if include_ancestors:
            result["ancestors"] = await self.store.get_ancestors(node_id, max_depth=10)
        if include_children:
            result["children"] = await self.store.get_descendants(node_id, max_depth=1)
        return result

    async def delete_node(self, *, node_id: str) -> dict[str, Any]:
        node = await self.store.get_node(node_id)
        if node is None:
            raise ValueError(f"Node {node_id} not found")
        await self.store.delete_node(node_id)
        return {"deleted": True, "id": node_id, "title": node["title"]}

    async def get_branch(
        self,
        *,
        root_node_id: str,
        max_depth: int = 2,
        include_edges: bool = True,
    ) -> dict[str, Any]:
        root = await self.store.get_node(root_node_id)
        if root is None:
            raise ValueError(f"Node {root_node_id} not found")

        descendants = await self.store.get_descendants(root_node_id, max_depth=max_depth)
        nodes = [root, *descendants]
        branch: dict[str, Any] = {
            "root": root,
            "nodes": nodes,
            "count": len(nodes),
            "max_depth": max_depth,
        }

        if include_edges:
            node_ids = {node["id"] for node in nodes}
            edge_map: dict[str, dict[str, Any]] = {}
            for node_id in node_ids:
                for edge in await self.store.get_edges(node_id):
                    if edge["from_node_id"] in node_ids and edge["to_node_id"] in node_ids:
                        edge_map[edge["id"]] = edge
            branch["edges"] = list(edge_map.values())

        return branch

    async def find_node_by_title(
        self,
        *,
        agent_id: str,
        title: str,
        exact: bool = True,
        domain: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        title_norm = title.strip().lower()
        if not title_norm:
            raise ValueError("Title query must not be empty")

        query_limit = max(limit * 10, 200)
        nodes = await self.store.get_nodes_by_agent(
            agent_id,
            domain=domain,
            limit=query_limit,
            offset=0,
        )
        if exact:
            matches = [node for node in nodes if node["title"].strip().lower() == title_norm]
        else:
            matches = [node for node in nodes if title_norm in node["title"].strip().lower()]

        matches.sort(
            key=lambda node: (
                node["title"].strip().lower() != title_norm,
                -node.get("confidence", 0.0),
                str(node.get("updated_at") or ""),
                str(node.get("created_at") or ""),
                node["title"],
            ),
            reverse=False,
        )
        limited_matches = matches[:limit]
        return {
            "query": title,
            "exact": exact,
            "domain": domain,
            "count": len(matches),
            "matches_returned": len(limited_matches),
            "ambiguous": len(matches) > 1,
            "selected_match": limited_matches[0] if limited_matches else None,
            "matches": limited_matches,
        }

    async def link_nodes(
        self,
        *,
        agent_id: str,
        from_node_id: str,
        to_node_id: str,
        edge_type: str,
        description: str,
        weight: float = 0.5,
        source_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not await self.store.get_node(from_node_id) or not await self.store.get_node(to_node_id):
            raise ValueError("Both nodes must exist before linking")
        self._validate_edge_type(edge_type)
        edge = await self.store.create_edge(
            agent_id,
            from_node_id,
            to_node_id,
            edge_type,
            weight=weight,
            description=description,
            source_id=source_id,
            metadata=metadata,
        )
        if edge_type == EdgeType.CONTRADICTS.value:
            from_node = await self.store.get_node(from_node_id)
            to_node = await self.store.get_node(to_node_id)
            await self.store.create_spark(
                agent_id,
                "contradiction",
                f"Contradiction: {from_node['title']} vs {to_node['title']}",
                priority=0.9,
                domain=from_node.get("domain") or to_node.get("domain"),
                target_node_id=from_node_id,
                source_id=source_id,
                metadata={"nodes": [from_node_id, to_node_id]},
            )
        return edge

    async def update_node(
        self,
        *,
        node_id: str,
        content: str | None = None,
        summary: str | None = None,
        confidence: float | None = None,
        parent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        consolidation_version: int | None = None,
    ) -> dict[str, Any]:
        existing = await self.store.get_node(node_id)
        if existing is None:
            raise ValueError(f"Node {node_id} not found")
        updates: dict[str, Any] = {"metadata": metadata or {}}
        if parent_id is not None:
            await self._validate_parent_id(
                agent_id=existing["agent_id"],
                parent_id=parent_id,
                node_id=node_id,
            )
            updates["parent_id"] = parent_id
        if content is not None:
            updates["content"] = content
            updates["embedding"] = await self.embedding_client.embed_text(existing["title"], content)
        if summary is not None:
            updates["summary"] = summary
        if confidence is not None:
            updates["confidence"] = confidence
        if consolidation_version is not None:
            updates["consolidation_version"] = consolidation_version
        return await self.store.update_node(node_id, **updates)

    async def _default_parent_id(self, agent_id: str) -> str | None:
        agent = await self.store.get_agent(agent_id)
        root_id = (agent.get("config") or {}).get("root_node_id") if agent else None
        if not root_id:
            return None
        root = await self.store.get_node(root_id)
        if root is None or root.get("agent_id") != agent_id:
            return None
        return root_id

    async def _link_related_nodes(
        self,
        agent_id: str,
        node: dict[str, Any],
        embedding: list[float] | None,
    ) -> list[dict[str, Any]]:
        if not embedding or (node.get("metadata") or {}).get("system"):
            return []

        candidates = await self.store.vector_search(agent_id, embedding, top_k=12)
        existing_edges = await self.store.get_edges(node["id"])
        connected_ids = {
            edge["to_node_id"] if edge["from_node_id"] == node["id"] else edge["from_node_id"]
            for edge in existing_edges
        }
        linked: list[dict[str, Any]] = []
        parent_id = node.get("parent_id")
        for candidate in candidates:
            if candidate["id"] == node["id"]:
                continue
            if candidate["id"] in connected_ids:
                continue
            if candidate.get("parent_id") == parent_id:
                continue
            if (candidate.get("metadata") or {}).get("system"):
                continue
            similarity = candidate.get("similarity", 0.0)
            if similarity < self.AUTO_LINK_MIN_SIMILARITY:
                continue
            decision = await self.relationship_judge.judge(node, candidate, similarity)
            if decision.edge_type is None:
                continue
            linked.append(
                await self.store.create_edge(
                    agent_id,
                    node["id"],
                    candidate["id"],
                    decision.edge_type,
                    weight=decision.confidence,
                    description=decision.description,
                    source_id=None,
                    metadata={
                        "generated_by": "relationship_judge" if decision.source == "llm" else "relationship_heuristic",
                        "similarity": similarity,
                        "judge_confidence": decision.confidence,
                    },
                )
            )
            if len(linked) >= self.AUTO_LINK_MAX_EDGES:
                break
        return linked

    async def _validate_parent_id(
        self,
        *,
        agent_id: str,
        parent_id: str,
        node_id: str | None = None,
    ) -> None:
        parent = await self.store.get_node(parent_id)
        if parent is None:
            raise ValueError(f"Parent node {parent_id} not found")
        if parent.get("agent_id") != agent_id:
            raise ValueError("Parent node belongs to a different agent")
        if node_id is not None:
            if parent_id == node_id:
                raise ValueError("A node cannot be its own parent")
            descendants = await self.store.get_descendants(node_id, max_depth=1000)
            if any(descendant["id"] == parent_id for descendant in descendants):
                raise ValueError("A node cannot be moved under one of its descendants")

    async def search_knowledge(
        self,
        *,
        agent_id: str,
        query: str,
        top_k: int = 10,
        hop_depth: int = 2,
        min_weight: float = 0.5,
        token_budget: int = 2000,
        node_type: str | None = None,
        domain: str | None = None,
        scope: str = "self",
    ) -> dict[str, Any]:
        query_embedding = await self.embedding_client.embed_text("query", query)
        return await self.assembler.assemble(
            agent_id=agent_id,
            query_embedding=query_embedding,
            query=query,
            top_k=top_k,
            hop_depth=hop_depth,
            min_weight=min_weight,
            token_budget=token_budget,
            node_type=node_type,
            domain=domain,
            scope=scope,
        )

    async def build_relationships(
        self,
        *,
        agent_id: str,
        limit: int = 200,
    ) -> dict[str, int]:
        nodes = await self.store.get_nodes_by_agent(agent_id, limit=limit)
        nodes_processed = 0
        edges_created = 0
        for node in nodes:
            if (node.get("metadata") or {}).get("system"):
                continue
            nodes_processed += 1
            edges_created += len(await self._link_related_nodes(agent_id, node, node.get("embedding")))
        return {"nodes_processed": nodes_processed, "edges_created": edges_created}

    async def reclassify_relationships(
        self,
        *,
        agent_id: str,
        limit: int = 2000,
    ) -> dict[str, int]:
        edges = await self.store.get_all_edges(agent_id, limit=limit)
        edges_processed = 0
        edges_updated = 0
        edges_skipped = 0
        for edge in edges:
            metadata = edge.get("metadata") or {}
            if metadata.get("generated_by") not in {"auto_link", "relationship_judge"}:
                continue
            source = await self.store.get_node(edge["from_node_id"])
            candidate = await self.store.get_node(edge["to_node_id"])
            if source is None or candidate is None:
                edges_skipped += 1
                continue
            similarity = float(metadata.get("similarity") or edge.get("weight") or 0.0)
            decision = await self.relationship_judge.judge(source, candidate, similarity)
            edges_processed += 1
            if decision.edge_type is None:
                edges_skipped += 1
                continue
            if decision.source != "llm" and metadata.get("generated_by") == "auto_link":
                edges_skipped += 1
                continue
            if (
                edge["edge_type"] == decision.edge_type
                and edge["description"] == decision.description
                and edge["weight"] == decision.confidence
            ):
                continue
            await self.store.update_edge(
                edge["id"],
                edge_type=decision.edge_type,
                description=decision.description,
                weight=decision.confidence,
                metadata={
                    "generated_by": "relationship_judge" if decision.source == "llm" else "relationship_heuristic",
                    "judge_confidence": decision.confidence,
                },
            )
            edges_updated += 1
        return {
            "edges_processed": edges_processed,
            "edges_updated": edges_updated,
            "edges_skipped": edges_skipped,
        }

    async def get_sparks(
        self,
        *,
        agent_id: str,
        status: str = "active",
        spark_type: str | None = None,
        domain: str | None = None,
        min_priority: float | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        return await self.store.get_sparks(
            agent_id,
            status=status,
            spark_type=spark_type,
            domain=domain,
            min_priority=min_priority,
            limit=limit,
        )

    async def resolve_spark(
        self,
        *,
        spark_id: str,
        node_ids: list[str] | None = None,
        notes: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        node_ids = node_ids or []
        for node_id in node_ids:
            if not await self.store.get_node(node_id):
                raise ValueError(f"Node {node_id} not found")
        return await self.store.resolve_spark(spark_id, node_ids, notes=notes, metadata=metadata)

    async def abandon_spark(
        self,
        *,
        spark_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return await self.store.abandon_spark(spark_id, reason=reason)

    async def configure_agent(
        self,
        *,
        agent_id: str,
        specialty: str | None = None,
        domains: list[str] | None = None,
        skill_notes: str | None = None,
        suggested_sources: list[str] | None = None,
        trigger_discovery: bool = True,
    ) -> dict[str, Any]:
        """Set the agent's research direction and activate the research pipeline.

        This is the primary activation point. Calling it with a specialty is all
        that's needed to start autonomous discovery — no other configuration required.

        Updates the agent root node content so the research direction is visible in
        the visualizer and searchable via semantic search. If trigger_discovery=True
        (default), fires an immediate background discovery run so the first batch of
        content arrives within seconds rather than waiting for the next scheduled cycle.
        """
        updates: dict[str, Any] = {}
        if specialty is not None:
            updates["specialty"] = specialty.strip()
        if domains is not None:
            updates["domains"] = [d.strip() for d in domains if d.strip()]
        if skill_notes is not None:
            updates["skill_notes"] = skill_notes.strip()
        existing_agent = await self.store.get_agent(agent_id)
        if suggested_sources is not None:
            existing_config = dict((existing_agent or {}).get("config") or {})
            existing_config["suggested_sources"] = [s.strip() for s in suggested_sources if s.strip()]
            updates["config"] = existing_config

        agent = await self.store.update_agent(agent_id, **updates)

        # Sync research direction to the agent root node so it's visible in the visualizer
        config = agent.get("config") or {}
        root_node_id = config.get("root_node_id")
        if root_node_id:
            parts: list[str] = []
            if agent.get("specialty"):
                parts.append(f"Research direction: {agent['specialty']}")
            if agent.get("domains"):
                parts.append(f"Core domains: {', '.join(agent['domains'])}")
            if agent.get("skill_notes"):
                parts.append(f"Skills & notes: {agent['skill_notes']}")
            suggested = (agent.get("config") or {}).get("suggested_sources") or []
            if suggested:
                parts.append(f"Suggested sources: {', '.join(suggested)}")
            if parts:
                content = "\n".join(parts)
                summary = agent.get("specialty") or parts[0]
                try:
                    await self.store.update_node(root_node_id, content=content, summary=summary)
                except Exception:
                    pass

        # Fire an immediate discovery run in the background so the first batch
        # of content arrives now rather than at the next scheduled tick.
        discovery_triggered = False
        if trigger_discovery and agent.get("specialty"):
            from neo.config import settings
            if settings.discovery_enabled:
                task = asyncio.create_task(self._run_discovery(agent))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
                discovery_triggered = True

        return {
            "id": agent["id"],
            "name": agent["name"],
            "specialty": agent.get("specialty"),
            "domains": agent.get("domains") or [],
            "skill_notes": agent.get("skill_notes"),
            "suggested_sources": (agent.get("config") or {}).get("suggested_sources") or [],
            "discovery_triggered": discovery_triggered,
        }

    async def _run_discovery(self, agent: dict[str, Any]) -> None:
        """Fire a single discovery pass — called in background after configure_agent."""
        try:
            from neo.config import settings
            from neo.core.discovery import DiscoveryJob
            from neo.core.youtube import YouTubeSearchClient, EchoSearchAsYouTube
            from neo.core.web_search import WebSearchClient, NullWebSearch

            yt_search = None
            if settings.youtube_api_key:
                yt_search = YouTubeSearchClient(settings.youtube_api_key)
            elif settings.search_api_key:
                yt_search = EchoSearchAsYouTube(
                    WebSearchClient(settings.search_provider, settings.search_api_key)
                )

            res_key = settings.llm_api_key_for("resolution")
            llm = None
            if settings.llm_configured_for("resolution"):
                from neo.core.resolver import ResolutionLLM
                llm = ResolutionLLM(
                    api_key=res_key,
                    model=settings.llm_model_for("resolution"),
                    base_url=settings.llm_base_url_for("resolution"),
                    provider=settings.llm_provider_for("resolution"),
                )

            job = DiscoveryJob(self, llm=llm, yt_search=yt_search)
            # Re-fetch agent to get latest config
            fresh = await self.store.get_agent(agent["id"])
            if fresh:
                await job.run(fresh, batch_size=settings.discovery_batch_size,
                              lookback_days=settings.discovery_lookback_days)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("configure_agent: background discovery failed: %s", exc)

    async def get_agent_info(self, *, agent_id: str) -> dict[str, Any]:
        """Return current agent configuration (specialty, domains, skill_notes)."""
        agent = await self.store.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")
        return {
            "id": agent["id"],
            "name": agent["name"],
            "specialty": agent.get("specialty"),
            "domains": agent.get("domains") or [],
            "skill_notes": agent.get("skill_notes"),
            "suggested_sources": (agent.get("config") or {}).get("suggested_sources") or [],
            "config": agent.get("config") or {},
        }

    async def get_activity_summary(
        self,
        *,
        agent_id: str,
        since: str | None = None,
    ) -> dict[str, Any]:
        since_dt = datetime.fromisoformat(since) if since else datetime.now(timezone.utc) - timedelta(days=1)
        if since_dt.tzinfo is None:
            since_dt = since_dt.replace(tzinfo=timezone.utc)
        activity = await self.store.get_activity(agent_id, since_dt)
        agent = await self.store.get_agent(agent_id)
        config = (agent.get("config") or {}) if agent else {}
        # Strip large fields (content, embedding) from recent_nodes to keep the
        # MCP response small enough for stdio transport (~64 KB limit in most clients).
        _OMIT = {"content", "embedding"}
        recent_nodes_slim = [
            {k: v for k, v in node.items() if k not in _OMIT}
            for node in activity["recent_nodes"]
        ]
        return {
            "period": {"since": since_dt.isoformat(), "until": datetime.now(timezone.utc).isoformat()},
            "root_node_id": config.get("root_node_id"),
            "agents_root_node_id": config.get("agents_root_node_id"),
            "counts": activity["counts"],
            "recent_nodes": recent_nodes_slim,
            "active_sparks": activity["active_sparks"],
            "contradictions": activity["contradictions"],
            "domains_active": activity["domains_active"],
        }

    async def _find_duplicate(
        self, agent_id: str, title: str, node_type: str
    ) -> dict[str, Any] | None:
        """Return the first node with an exact (case-insensitive) title+type match, or None."""
        title_norm = title.strip().lower()
        # Fetch only nodes of this type to keep the scan small
        candidates = await self.store.get_nodes_by_agent(agent_id, node_type=node_type, limit=500)
        return next(
            (n for n in candidates if n["title"].strip().lower() == title_norm),
            None,
        )

    @staticmethod
    def _summarize(content: str) -> str:
        words = content.strip().split()
        return " ".join(words[:24]) if words else ""

    @staticmethod
    def _validate_node_type(node_type: str) -> None:
        if node_type not in {item.value for item in NodeType}:
            raise ValueError(f"Invalid node_type: {node_type}")

    @staticmethod
    def _validate_edge_type(edge_type: str) -> None:
        if edge_type not in {item.value for item in EdgeType}:
            raise ValueError(f"Invalid edge_type: {edge_type}")
