from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from neo.store.interface import StoreInterface


class WorkingMemoryAssembler:
    def __init__(self, store: StoreInterface):
        self.store = store

    async def assemble(
        self,
        *,
        agent_id: str,
        query_embedding: list[float],
        query: str,
        top_k: int = 10,
        hop_depth: int = 2,
        min_weight: float = 0.5,
        token_budget: int = 2000,
        node_type: str | None = None,
        domain: str | None = None,
        scope: str = "self",
    ) -> dict[str, Any]:
        seeds = await self.store.vector_search(
            agent_id,
            query_embedding,
            top_k=top_k,
            node_type=node_type,
            domain=domain,
            scope=scope,
        )
        if not seeds:
            return {"nodes": [], "edges": [], "contradictions": [], "sparks": [], "total_candidates": 0}

        collected_nodes: dict[str, dict[str, Any]] = {}
        collected_edges: dict[str, dict[str, Any]] = {}
        contradiction_edges: list[dict[str, Any]] = []

        for seed in seeds:
            collected_nodes[seed["id"]] = seed
            neighborhood = await self.store.get_neighborhood(
                seed["id"],
                depth=hop_depth,
                min_weight=min_weight,
                edge_types=None,
            )
            for node in neighborhood["nodes"]:
                collected_nodes[node["id"]] = node
            for edge in neighborhood["edges"]:
                collected_edges[edge["id"]] = edge
                if edge["edge_type"] == "contradicts":
                    contradiction_edges.append(edge)
            for ancestor in await self.store.get_ancestors(seed["id"], max_depth=hop_depth):
                collected_nodes[ancestor["id"]] = ancestor

        ranked = sorted(
            collected_nodes.values(),
            key=lambda node: self._rank_node(node, seeds, contradiction_edges),
            reverse=True,
        )

        chosen: list[dict[str, Any]] = []
        budget = 0
        for node in ranked:
            cost = len((node.get("summary") or "").split()) + len((node.get("title") or "").split())
            if chosen and budget + cost > token_budget:
                break
            chosen.append(node)
            budget += cost

        spark_domain = domain or (chosen[0].get("domain") if chosen else None)
        sparks = await self.store.get_sparks(agent_id, status="active", domain=spark_domain, limit=5)

        return {
            "nodes": [
                {
                    "id": node["id"],
                    "title": node["title"],
                    "summary": node["summary"],
                    "confidence": node["confidence"],
                    "domain": node.get("domain"),
                }
                for node in chosen
            ],
            "edges": list(collected_edges.values()),
            "contradictions": contradiction_edges,
            "sparks": sparks,
            "total_candidates": len(collected_nodes),
            "query": query,
        }

    @staticmethod
    def _rank_node(
        node: dict[str, Any],
        seeds: list[dict[str, Any]],
        contradictions: list[dict[str, Any]],
    ) -> float:
        seed_similarity = next((seed.get("similarity", 0.0) for seed in seeds if seed["id"] == node["id"]), 0.0)
        confidence = node.get("confidence", 0.0)
        updated_at = node.get("updated_at")
        if updated_at and updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age_hours = 9999.0
        if updated_at:
            age_hours = max((datetime.now(timezone.utc) - updated_at).total_seconds() / 3600, 0.0)
        recency = 1.0 / (1.0 + age_hours / 24.0)
        contradiction_penalty = 0.2 if any(node["id"] in {edge["from_node_id"], edge["to_node_id"]} for edge in contradictions) else 0.0
        return (seed_similarity * 0.6) + (confidence * 0.2) + (recency * 0.2) - contradiction_penalty
