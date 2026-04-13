from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StoreNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_type: str
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    summary: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    parent_id: str | None = None
    source_id: str | None = None
    spark_id: str | None = None
    domain: str | None = None
    metadata: dict[str, Any] | None = None


class LinkNodesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_node_id: str
    to_node_id: str
    edge_type: str
    description: str = Field(min_length=1)
    weight: float = Field(default=0.5, ge=0.0, le=1.0)
    source_id: str | None = None
    metadata: dict[str, Any] | None = None


class UpdateNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    summary: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    parent_id: str | None = None
    metadata: dict[str, Any] | None = None
    consolidation_version: int | None = Field(default=None, ge=0)


class MoveNodeRequest(BaseModel):
    parent_id: str | None  # None = move to root


class SearchKnowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    top_k: int = Field(default=10, ge=1, le=100)
    hop_depth: int = Field(default=2, ge=0, le=5)
    min_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    token_budget: int = Field(default=2000, ge=1, le=20000)
    node_type: str | None = None
    domain: str | None = None


class ResolveSparkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_ids: list[str] | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None


class ConfigureAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialty: str | None = None
    domains: list[str] | None = None
    skill_notes: str | None = None
    suggested_sources: list[str] | None = None


class HealthResponse(BaseModel):
    status: str
    agent_name: str
    db_scheme: str
    consolidation_enabled: bool
    embedding_provider: str
    embedding_fallback_enabled: bool
