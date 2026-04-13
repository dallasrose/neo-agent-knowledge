from __future__ import annotations

from collections import defaultdict
from typing import Any, Protocol

from neo.core.sparks import SparkGenerator
from neo.store.interface import StoreInterface


class PerNodeConsolidator(Protocol):
    async def refine(self, node: dict[str, Any], neighborhood: dict[str, Any]) -> dict[str, Any]:
        ...


class CrossNodeConsolidator(Protocol):
    async def synthesize(self, domain: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
        ...


class NullPerNodeConsolidator:
    async def refine(self, node: dict[str, Any], neighborhood: dict[str, Any]) -> dict[str, Any]:
        return {
            "summary": node["summary"],
            "confidence": node["confidence"],
            "content": node["content"],
            "skill_notes": None,
        }


class NullCrossNodeConsolidator:
    async def synthesize(self, domain: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
        return {"synthesis_nodes": [], "sparks": []}


class ConsolidationEngine:
    def __init__(
        self,
        store: StoreInterface,
        *,
        per_node_model: PerNodeConsolidator | None = None,
        cross_node_model: CrossNodeConsolidator | None = None,
        spark_generator: SparkGenerator | None = None,
    ) -> None:
        self.store = store
        self.per_node_model = per_node_model or NullPerNodeConsolidator()
        self.cross_node_model = cross_node_model or NullCrossNodeConsolidator()
        self.spark_generator = spark_generator or SparkGenerator(store)

    async def run(
        self,
        agent_id: str,
        *,
        since_version: int = 0,
        limit: int = 100,
        agent_root_node_id: str | None = None,
    ) -> dict[str, Any]:
        agent = await self.store.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")

        # Resolve root node from agent config if not passed explicitly
        if agent_root_node_id is None:
            agent_root_node_id = (agent.get("config") or {}).get("root_node_id")

        candidates = await self.store.get_unconsolidated_nodes(agent_id, since_version=since_version, limit=limit)
        nodes_processed = 0
        edges_updated = 0
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for node in candidates:
            neighborhood = await self.store.get_neighborhood(node["id"], depth=1, min_weight=0.0)
            refined = await self.per_node_model.refine(node, neighborhood)
            await self.store.update_node(
                node["id"],
                content=refined.get("content", node["content"]),
                summary=refined.get("summary", node["summary"]),
                confidence=refined.get("confidence", node["confidence"]),
                consolidation_version=node["consolidation_version"] + 1,
                metadata={"consolidated": True},
            )
            grouped[node.get("domain") or "default"].append(await self.store.get_node(node["id"]))
            nodes_processed += 1

        syntheses_created = 0
        sparks_generated = 0
        for domain, nodes in grouped.items():
            synthesis = await self.cross_node_model.synthesize(domain, nodes)
            for synthesis_node in synthesis.get("synthesis_nodes", []):
                created = await self.store.create_node(
                    agent_id,
                    "synthesis",
                    synthesis_node["title"],
                    synthesis_node["content"],
                    summary=synthesis_node["summary"],
                    confidence=synthesis_node.get("confidence", 0.7),
                    parent_id=agent_root_node_id,
                    source_id=None,
                    spark_id=None,
                    embedding=synthesis_node.get("embedding"),
                    domain=domain,
                    metadata={"generated_by": "consolidation"},
                )
                syntheses_created += 1
                for source_node_id in synthesis_node.get("source_node_ids", []):
                    await self.store.create_edge(
                        agent_id,
                        created["id"],
                        source_node_id,
                        "supports",
                        weight=0.7,
                        description="Consolidation synthesis",
                        source_id=None,
                        metadata={"generated_by": "consolidation"},
                    )
                    edges_updated += 1
                source_ids = synthesis_node.get("source_node_ids", [])
                if source_ids:
                    await self.store.mark_consolidated(source_ids)
            if synthesis.get("sparks"):
                created_sparks = await self.spark_generator.generate_on_consolidation(agent, synthesis["sparks"])
                sparks_generated += len(created_sparks)

        return {
            "nodes_processed": nodes_processed,
            "syntheses_created": syntheses_created,
            "sparks_generated": sparks_generated,
            "edges_updated": edges_updated,
        }
