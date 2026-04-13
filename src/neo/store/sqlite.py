from __future__ import annotations

import json
import math
from collections import deque
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, delete as sql_delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from neo.models import NeoAgent, NeoEdge, NeoNode, NeoSource, NeoSpark
from neo.store.interface import StoreInterface


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SQLiteStore(StoreInterface):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def get_or_create_agent(self, name: str, **kwargs: Any) -> dict[str, Any]:
        async with self.session_factory() as session:
            result = await session.execute(select(NeoAgent).where(NeoAgent.name == name))
            agent = result.scalar_one_or_none()
            if agent is None:
                agent = NeoAgent(name=name, **kwargs)
                session.add(agent)
                await session.commit()
                await session.refresh(agent)
            return self._agent_to_dict(agent)

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        async with self.session_factory() as session:
            agent = await session.get(NeoAgent, agent_id)
            return self._agent_to_dict(agent) if agent else None

    async def update_agent(self, agent_id: str, **kwargs: Any) -> dict[str, Any]:
        async with self.session_factory() as session:
            agent = await session.get(NeoAgent, agent_id)
            if agent is None:
                raise ValueError(f"Agent {agent_id} not found")
            for key, value in kwargs.items():
                setattr(agent, key, value)
            await session.commit()
            await session.refresh(agent)
            return self._agent_to_dict(agent)

    async def get_agent_by_name(self, name: str) -> dict[str, Any] | None:
        async with self.session_factory() as session:
            result = await session.execute(select(NeoAgent).where(NeoAgent.name == name))
            agent = result.scalar_one_or_none()
            return self._agent_to_dict(agent) if agent else None

    async def delete_agent(self, agent_id: str) -> bool:
        async with self.session_factory() as session:
            agent = await session.get(NeoAgent, agent_id)
            if agent is None:
                return False
            await session.delete(agent)
            await session.commit()
            return True

    async def create_node(
        self,
        agent_id: str,
        node_type: str,
        title: str,
        content: str,
        *,
        summary: str,
        confidence: float,
        parent_id: str | None,
        source_id: str | None,
        spark_id: str | None,
        embedding: list[float] | None,
        domain: str | None,
        metadata: dict[str, Any] | None,
        status: str = "active",
    ) -> dict[str, Any]:
        async with self.session_factory() as session:
            node = NeoNode(
                agent_id=agent_id,
                node_type=node_type,
                title=title,
                content=content,
                summary=summary,
                confidence=confidence,
                parent_id=parent_id,
                source_id=source_id,
                spark_id=spark_id,
                embedding=self._encode_embedding(embedding),
                domain=domain,
                metadata_=metadata or {},
                status=status,
            )
            session.add(node)
            await session.commit()
            await session.refresh(node)
            return self._node_to_dict(node)

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        async with self.session_factory() as session:
            node = await session.get(NeoNode, node_id)
            return self._node_to_dict(node) if node else None

    async def update_node(self, node_id: str, **kwargs: Any) -> dict[str, Any]:
        async with self.session_factory() as session:
            node = await session.get(NeoNode, node_id)
            if node is None:
                raise ValueError(f"Node {node_id} not found")

            history = dict(node.metadata_ or {})
            if "content" in kwargs or "summary" in kwargs:
                history.setdefault("history", []).append(
                    {
                        "content": node.content,
                        "summary": node.summary,
                        "updated_at": node.updated_at.isoformat() if node.updated_at else None,
                    }
                )

            for key, value in kwargs.items():
                if key == "embedding":
                    setattr(node, "embedding", self._encode_embedding(value))
                elif key == "metadata":
                    history.update(value or {})
                else:
                    setattr(node, key, value)

            node.metadata_ = history
            await session.commit()
            await session.refresh(node)
            return self._node_to_dict(node)

    async def get_nodes_by_agent(
        self,
        agent_id: str,
        *,
        node_type: str | None = None,
        domain: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            query = select(NeoNode).where(NeoNode.agent_id == agent_id)
            if node_type:
                query = query.where(NeoNode.node_type == node_type)
            if domain:
                query = query.where(NeoNode.domain == domain)
            if since:
                query = query.where(NeoNode.updated_at >= since)
            query = query.order_by(NeoNode.updated_at.desc()).offset(offset).limit(limit)
            result = await session.execute(query)
            return [self._node_to_dict(node) for node in result.scalars().all()]

    async def create_edge(
        self,
        agent_id: str,
        from_node_id: str,
        to_node_id: str,
        edge_type: str,
        *,
        weight: float,
        description: str,
        source_id: str | None,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        async with self.session_factory() as session:
            edge = NeoEdge(
                agent_id=agent_id,
                from_node_id=from_node_id,
                to_node_id=to_node_id,
                edge_type=edge_type,
                weight=weight,
                description=description,
                source_id=source_id,
                metadata_=metadata or {},
            )
            session.add(edge)
            await session.commit()
            await session.refresh(edge)
            return self._edge_to_dict(edge)

    async def get_edges(
        self,
        node_id: str,
        *,
        direction: str = "both",
        edge_type: str | None = None,
    ) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            query = select(NeoEdge)
            if direction == "out":
                query = query.where(NeoEdge.from_node_id == node_id)
            elif direction == "in":
                query = query.where(NeoEdge.to_node_id == node_id)
            else:
                query = query.where(or_(NeoEdge.from_node_id == node_id, NeoEdge.to_node_id == node_id))
            if edge_type:
                query = query.where(NeoEdge.edge_type == edge_type)
            result = await session.execute(query.order_by(NeoEdge.created_at.asc()))
            return [self._edge_to_dict(edge) for edge in result.scalars().all()]

    async def delete_node(self, node_id: str) -> bool:
        async with self.session_factory() as session:
            # Check existence first
            exists = await session.get(NeoNode, node_id)
            if exists is None:
                return False
            # Delete node and all descendants via recursive CTE
            await session.execute(text("""
                DELETE FROM neo_nodes WHERE id IN (
                    WITH RECURSIVE descendants(id) AS (
                        SELECT :node_id
                        UNION ALL
                        SELECT n.id FROM neo_nodes n
                        JOIN descendants d ON n.parent_id = d.id
                    )
                    SELECT id FROM descendants
                )
            """), {"node_id": node_id})
            await session.commit()
            return True

    async def get_all_edges(self, agent_id: str, *, limit: int = 2000) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            # Join through from_node to filter by agent
            subquery = select(NeoNode.id).where(NeoNode.agent_id == agent_id).scalar_subquery()
            query = (
                select(NeoEdge)
                .where(NeoEdge.from_node_id.in_(subquery))
                .order_by(NeoEdge.created_at.asc())
                .limit(limit)
            )
            result = await session.execute(query)
            return [self._edge_to_dict(edge) for edge in result.scalars().all()]

    async def create_source(
        self,
        agent_id: str,
        source_type: str,
        title: str,
        reference: str,
        *,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self.session_factory() as session:
            source = NeoSource(
                agent_id=agent_id,
                source_type=source_type,
                title=title,
                reference=reference,
                content=content,
                metadata_=metadata or {},
            )
            session.add(source)
            await session.commit()
            await session.refresh(source)
            return self._source_to_dict(source)

    async def get_source(self, source_id: str) -> dict[str, Any] | None:
        async with self.session_factory() as session:
            source = await session.get(NeoSource, source_id)
            return self._source_to_dict(source) if source else None

    async def create_spark(
        self,
        agent_id: str,
        spark_type: str,
        description: str,
        *,
        priority: float,
        domain: str | None,
        target_node_id: str | None,
        source_id: str | None,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        async with self.session_factory() as session:
            spark = NeoSpark(
                agent_id=agent_id,
                spark_type=spark_type,
                description=description,
                priority=priority,
                domain=domain,
                target_node_id=target_node_id,
                source_id=source_id,
                metadata_=metadata or {},
            )
            session.add(spark)
            await session.commit()
            await session.refresh(spark)
            return self._spark_to_dict(spark)

    async def get_sparks(
        self,
        agent_id: str,
        *,
        status: str = "active",
        spark_type: str | None = None,
        domain: str | None = None,
        min_priority: float | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            query = select(NeoSpark).where(NeoSpark.agent_id == agent_id)
            if status:
                query = query.where(NeoSpark.status == status)
            if spark_type:
                query = query.where(NeoSpark.spark_type == spark_type)
            if domain:
                query = query.where(NeoSpark.domain == domain)
            if min_priority is not None:
                query = query.where(NeoSpark.priority >= min_priority)
            result = await session.execute(query.order_by(NeoSpark.priority.desc(), NeoSpark.created_at.desc()).limit(limit))
            return [self._spark_to_dict(spark) for spark in result.scalars().all()]

    async def resolve_spark(self, spark_id: str, resolved_node_ids: list[str], *, notes: str | None = None) -> dict[str, Any]:
        async with self.session_factory() as session:
            spark = await session.get(NeoSpark, spark_id)
            if spark is None:
                raise ValueError(f"Spark {spark_id} not found")
            spark.status = "resolved"
            spark.resolved_at = _utcnow()
            spark.metadata_ = {
                **(spark.metadata_ or {}),
                "resolved_node_ids": resolved_node_ids,
                **({"notes": notes} if notes else {}),
            }
            if resolved_node_ids:
                spark.resolved_node_id = resolved_node_ids[0]
                nodes = (
                    await session.execute(select(NeoNode).where(NeoNode.id.in_(resolved_node_ids)))
                ).scalars().all()
                for node in nodes:
                    node.spark_id = spark.id
            await session.commit()
            await session.refresh(spark)
            return self._spark_to_dict(spark)

    async def abandon_spark(self, spark_id: str) -> dict[str, Any]:
        async with self.session_factory() as session:
            spark = await session.get(NeoSpark, spark_id)
            if spark is None:
                raise ValueError(f"Spark {spark_id} not found")
            spark.status = "abandoned"
            spark.resolved_at = _utcnow()
            await session.commit()
            await session.refresh(spark)
            return self._spark_to_dict(spark)

    async def vector_search(
        self,
        agent_id: str,
        query_embedding: list[float],
        *,
        top_k: int = 10,
        node_type: str | None = None,
        domain: str | None = None,
        min_confidence: float | None = None,
        status: str = "active",
        scope: str = "self",
    ) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            query = select(NeoNode)
            if scope != "network":
                query = query.where(NeoNode.agent_id == agent_id)
            if node_type:
                query = query.where(NeoNode.node_type == node_type)
            if domain:
                query = query.where(NeoNode.domain == domain)
            if min_confidence is not None:
                query = query.where(NeoNode.confidence >= min_confidence)
            if status:
                query = query.where(NeoNode.status == status)
            result = await session.execute(query)
            scored: list[tuple[float, NeoNode]] = []
            for node in result.scalars().all():
                embedding = self._decode_embedding(node.embedding)
                if not embedding:
                    continue
                scored.append((self._cosine_similarity(query_embedding, embedding), node))
            scored.sort(key=lambda item: item[0], reverse=True)
            return [
                {
                    **self._node_to_dict(node),
                    "similarity": similarity,
                }
                for similarity, node in scored[:top_k]
            ]

    async def get_neighborhood(
        self,
        node_id: str,
        *,
        depth: int = 1,
        min_weight: float = 0.0,
        edge_types: list[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        async with self.session_factory() as session:
            nodes_seen: set[str] = {node_id}
            edges_seen: set[str] = set()
            queue: deque[tuple[str, int]] = deque([(node_id, 0)])

            while queue:
                current_id, current_depth = queue.popleft()
                if current_depth >= depth:
                    continue
                query = select(NeoEdge).where(
                    and_(
                        or_(NeoEdge.from_node_id == current_id, NeoEdge.to_node_id == current_id),
                        NeoEdge.weight >= min_weight,
                    )
                )
                if edge_types:
                    query = query.where(NeoEdge.edge_type.in_(edge_types))
                result = await session.execute(query)
                for edge in result.scalars().all():
                    edges_seen.add(edge.id)
                    neighbor_id = edge.to_node_id if edge.from_node_id == current_id else edge.from_node_id
                    if neighbor_id not in nodes_seen:
                        nodes_seen.add(neighbor_id)
                        queue.append((neighbor_id, current_depth + 1))

            node_rows = (
                await session.execute(select(NeoNode).where(NeoNode.id.in_(nodes_seen)))
            ).scalars().all()
            edge_rows = (
                await session.execute(select(NeoEdge).where(NeoEdge.id.in_(edges_seen)))
            ).scalars().all()
            return {
                "nodes": [self._node_to_dict(node) for node in node_rows],
                "edges": [self._edge_to_dict(edge) for edge in edge_rows],
            }

    async def get_ancestors(self, node_id: str, max_depth: int = 10) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            current = await session.get(NeoNode, node_id)
            ancestors: list[dict[str, Any]] = []
            depth = 0
            while current and current.parent_id and depth < max_depth:
                parent = await session.get(NeoNode, current.parent_id)
                if parent is None:
                    break
                ancestors.append(self._node_to_dict(parent))
                current = parent
                depth += 1
            return ancestors

    async def get_descendants(self, node_id: str, max_depth: int = 10) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            descendants: list[dict[str, Any]] = []
            queue: deque[tuple[str, int]] = deque([(node_id, 0)])
            while queue:
                current_id, current_depth = queue.popleft()
                if current_depth >= max_depth:
                    continue
                result = await session.execute(select(NeoNode).where(NeoNode.parent_id == current_id))
                children = result.scalars().all()
                for child in children:
                    descendants.append(self._node_to_dict(child))
                    queue.append((child.id, current_depth + 1))
            return descendants

    async def get_activity(self, agent_id: str, since: datetime) -> dict[str, Any]:
        async with self.session_factory() as session:
            nodes_created = (
                await session.execute(
                    select(NeoNode).where(NeoNode.agent_id == agent_id, NeoNode.created_at >= since)
                )
            ).scalars().all()
            nodes_updated = (
                await session.execute(
                    select(NeoNode).where(
                        NeoNode.agent_id == agent_id,
                        NeoNode.updated_at >= since,
                    )
                )
            ).scalars().all()
            edges_created = (
                await session.execute(
                    select(NeoEdge).where(NeoEdge.agent_id == agent_id, NeoEdge.created_at >= since)
                )
            ).scalars().all()
            sparks_generated = (
                await session.execute(
                    select(NeoSpark).where(NeoSpark.agent_id == agent_id, NeoSpark.created_at >= since)
                )
            ).scalars().all()
            sparks_resolved = (
                await session.execute(
                    select(NeoSpark).where(
                        NeoSpark.agent_id == agent_id,
                        NeoSpark.status == "resolved",
                        NeoSpark.resolved_at >= since,
                    )
                )
            ).scalars().all()
            contradictions = (
                await session.execute(
                    select(NeoEdge).where(
                        NeoEdge.agent_id == agent_id,
                        NeoEdge.edge_type == "contradicts",
                    )
                )
            ).scalars().all()
            domains_active = (
                await session.execute(
                    select(NeoNode.domain, func.count(NeoNode.id))
                    .where(NeoNode.agent_id == agent_id, NeoNode.updated_at >= since)
                    .group_by(NeoNode.domain)
                )
            ).all()

            return {
                "counts": {
                    "nodes_created": len(nodes_created),
                    "nodes_updated": len([node for node in nodes_updated if (node.metadata_ or {}).get("history")]),
                    "edges_created": len(edges_created),
                    "sparks_generated": len(sparks_generated),
                    "sparks_resolved": len(sparks_resolved),
                },
                "recent_nodes": [self._node_to_dict(node) for node in nodes_created[:10]],
                "active_sparks": await self.get_sparks(agent_id, status="active", limit=10),
                "contradictions": [self._edge_to_dict(edge) for edge in contradictions],
                "domains_active": [
                    {"domain": domain, "count": count}
                    for domain, count in domains_active
                    if domain is not None
                ],
            }

    async def get_unconsolidated_nodes(
        self,
        agent_id: str,
        *,
        since_version: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(NeoNode)
                .where(
                    NeoNode.agent_id == agent_id,
                    NeoNode.consolidation_version <= since_version,
                )
                .order_by(NeoNode.updated_at.asc())
                .limit(limit)
            )
            return [self._node_to_dict(node) for node in result.scalars().all()]

    async def count_nodes_since(self, agent_id: str, since: datetime) -> int:
        async with self.session_factory() as session:
            result = await session.execute(
                select(func.count(NeoNode.id)).where(NeoNode.agent_id == agent_id, NeoNode.created_at >= since)
            )
            return int(result.scalar_one())

    async def get_nodes_without_sparks(
        self,
        agent_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return nodes that have no associated sparks of any status."""
        async with self.session_factory() as session:
            # LEFT JOIN sparks, keep only nodes where the join found nothing
            subquery = (
                select(NeoSpark.target_node_id)
                .where(NeoSpark.agent_id == agent_id)
                .where(NeoSpark.target_node_id.isnot(None))
                .distinct()
                .scalar_subquery()
            )
            query = (
                select(NeoNode)
                .where(NeoNode.agent_id == agent_id)
                .where(NeoNode.id.notin_(subquery))
                .order_by(NeoNode.created_at.desc())  # newest first — fresh nodes need sparks most
                .offset(offset)
                .limit(limit)
            )
            result = await session.execute(query)
            return [self._node_to_dict(n) for n in result.scalars().all()]

    async def mark_consolidated(self, node_ids: list[str]) -> None:
        if not node_ids:
            return
        async with self.session_factory() as session:
            placeholders = ", ".join(f":id{i}" for i in range(len(node_ids)))
            params: dict = {"now": datetime.now(timezone.utc)}
            for i, nid in enumerate(node_ids):
                params[f"id{i}"] = nid
            await session.execute(
                text(f"UPDATE neo_nodes SET status = 'consolidated', last_consolidated_at = :now, updated_at = :now WHERE id IN ({placeholders})"),
                params,
            )
            await session.commit()

    async def get_active_sparks_for_resolution(
        self, agent_id: str, limit: int = 3, min_priority: float = 0.5
    ) -> list[dict]:
        """Return high-priority sparks, biased toward those attached to recent nodes.

        Score = (base priority * 0.7) + (recency bonus * 0.3)
        Recency bonus: 1.0 if target node created within 7 days,
                       0.5 if within 30 days, 0.0 otherwise.
        This ensures fresh knowledge gets explored first while still
        respecting the structural priority (contradiction > open_question > weak_edge).
        """
        async with self.session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT s.id, s.agent_id, s.target_node_id, s.spark_type,
                           s.description, s.priority, s.status,
                           n.title as target_title, n.content as target_content,
                           n.summary as target_summary,
                           n.created_at as node_created_at,
                           n.domain as node_domain,
                           (s.priority * 0.7 +
                            CASE
                              WHEN n.created_at >= datetime('now', '-7 days')  THEN 0.30
                              WHEN n.created_at >= datetime('now', '-30 days') THEN 0.15
                              ELSE 0.0
                            END
                           ) as composite_score
                    FROM neo_sparks s
                    LEFT JOIN neo_nodes n ON s.target_node_id = n.id
                    WHERE s.agent_id = :agent_id
                      AND s.status = 'active'
                      AND s.priority >= :min_priority
                    ORDER BY composite_score DESC
                    LIMIT :limit
                """),
                {"agent_id": agent_id, "min_priority": min_priority, "limit": limit}
            )
            rows = result.mappings().all()
            return [dict(r) for r in rows]

    def _session(self):
        return self.session_factory()

    @staticmethod
    def _encode_embedding(embedding: list[float] | None) -> str | None:
        if embedding is None:
            return None
        return json.dumps(embedding)

    @staticmethod
    def _decode_embedding(embedding: str | None) -> list[float]:
        if not embedding:
            return []
        return [float(value) for value in json.loads(embedding)]

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        length = min(len(left), len(right))
        left = left[:length]
        right = right[:length]
        numerator = sum(a * b for a, b in zip(left, right, strict=False))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if not left_norm or not right_norm:
            return 0.0
        return numerator / (left_norm * right_norm)

    def _agent_to_dict(self, agent: NeoAgent) -> dict[str, Any]:
        return {
            "id": agent.id,
            "name": agent.name,
            "specialty": agent.specialty,
            "domains": agent.domains,
            "skill_notes": agent.skill_notes,
            "config": agent.config,
            "created_at": agent.created_at,
            "updated_at": agent.updated_at,
        }

    def _node_to_dict(self, node: NeoNode) -> dict[str, Any]:
        return {
            "id": node.id,
            "agent_id": node.agent_id,
            "node_type": node.node_type,
            "title": node.title,
            "content": node.content,
            "summary": node.summary,
            "confidence": node.confidence,
            "parent_id": node.parent_id,
            "source_id": node.source_id,
            "spark_id": node.spark_id,
            "embedding": self._decode_embedding(node.embedding),
            "domain": node.domain,
            "metadata": node.metadata_ or {},
            "created_at": node.created_at,
            "updated_at": node.updated_at,
            "consolidation_version": node.consolidation_version,
            "last_consolidated_at": node.last_consolidated_at,
            "status": node.status,
        }

    @staticmethod
    def _edge_to_dict(edge: NeoEdge) -> dict[str, Any]:
        return {
            "id": edge.id,
            "agent_id": edge.agent_id,
            "from_node_id": edge.from_node_id,
            "to_node_id": edge.to_node_id,
            "edge_type": edge.edge_type,
            "weight": edge.weight,
            "description": edge.description,
            "source_id": edge.source_id,
            "metadata": edge.metadata_ or {},
            "created_at": edge.created_at,
            "updated_at": edge.updated_at,
        }

    @staticmethod
    def _source_to_dict(source: NeoSource) -> dict[str, Any]:
        return {
            "id": source.id,
            "agent_id": source.agent_id,
            "source_type": source.source_type,
            "title": source.title,
            "reference": source.reference,
            "content": source.content,
            "metadata": source.metadata_ or {},
            "retrieved_at": source.retrieved_at,
        }

    @staticmethod
    def _spark_to_dict(spark: NeoSpark) -> dict[str, Any]:
        return {
            "id": spark.id,
            "agent_id": spark.agent_id,
            "spark_type": spark.spark_type,
            "target_node_id": spark.target_node_id,
            "description": spark.description,
            "priority": spark.priority,
            "status": spark.status,
            "source_id": spark.source_id,
            "resolved_node_id": spark.resolved_node_id,
            "domain": spark.domain,
            "metadata": spark.metadata_ or {},
            "created_at": spark.created_at,
            "resolved_at": spark.resolved_at,
        }
