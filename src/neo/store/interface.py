from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any


class StoreInterface(ABC):
    @abstractmethod
    async def get_or_create_agent(self, name: str, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def update_agent(self, agent_id: str, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def list_agents(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def update_node(self, node_id: str, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    async def get_edges(
        self,
        node_id: str,
        *,
        direction: str = "both",
        edge_type: str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def update_edge(self, edge_id: str, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    async def get_source(self, source_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    async def resolve_spark(
        self,
        spark_id: str,
        resolved_node_ids: list[str],
        *,
        notes: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def abandon_spark(
        self,
        spark_id: str,
        *,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    async def get_neighborhood(
        self,
        node_id: str,
        *,
        depth: int = 1,
        min_weight: float = 0.0,
        edge_types: list[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        raise NotImplementedError

    @abstractmethod
    async def get_ancestors(self, node_id: str, max_depth: int = 10) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def get_descendants(self, node_id: str, max_depth: int = 10) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def get_activity(self, agent_id: str, since: datetime) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def get_unconsolidated_nodes(
        self,
        agent_id: str,
        *,
        since_version: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def count_nodes_since(self, agent_id: str, since: datetime) -> int:
        raise NotImplementedError

    @abstractmethod
    async def delete_node(self, node_id: str) -> bool:
        """Delete a node. Returns True if it existed, False if not found.
        Edges cascade automatically. Child nodes' parent_id becomes NULL.
        Spark references (target_node_id, resolved_node_id) become NULL."""
        raise NotImplementedError

    @abstractmethod
    async def get_all_edges(
        self,
        agent_id: str,
        *,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        """Return all edges whose from_node belongs to this agent."""
        raise NotImplementedError

    @abstractmethod
    async def get_nodes_without_sparks(
        self,
        agent_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return nodes that have no associated sparks (any status)."""

    @abstractmethod
    async def get_agent_by_name(self, name: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def delete_agent(self, agent_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def mark_consolidated(self, node_ids: list[str]) -> None:
        """Mark nodes as consolidated (status = 'consolidated')."""
        raise NotImplementedError

    @abstractmethod
    async def get_active_sparks_for_resolution(
        self, agent_id: str, limit: int = 3, min_priority: float = 0.5
    ) -> list[dict]:
        """Return active sparks above min_priority, joined with target node data."""
        raise NotImplementedError
