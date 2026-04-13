from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from neo.db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def uuid_str() -> str:
    return str(uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class NeoAgent(TimestampMixin, Base):
    __tablename__ = "neo_agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    specialty: Mapped[str | None] = mapped_column(Text, nullable=True)
    domains: Mapped[list[str]] = mapped_column(JSON, default=list)
    skill_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    nodes: Mapped[list["NeoNode"]] = relationship(back_populates="agent")
    edges: Mapped[list["NeoEdge"]] = relationship(back_populates="agent")
    sources: Mapped[list["NeoSource"]] = relationship(back_populates="agent")
    sparks: Mapped[list["NeoSpark"]] = relationship(
        back_populates="agent",
        foreign_keys="NeoSpark.agent_id",
    )


class NeoSource(Base):
    __tablename__ = "neo_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    agent_id: Mapped[str] = mapped_column(ForeignKey("neo_agents.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(255))
    reference: Mapped[str] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    agent: Mapped["NeoAgent"] = relationship(back_populates="sources")
    nodes: Mapped[list["NeoNode"]] = relationship(back_populates="source")
    edges: Mapped[list["NeoEdge"]] = relationship(back_populates="source")
    sparks: Mapped[list["NeoSpark"]] = relationship(back_populates="source")


class NeoNode(TimestampMixin, Base):
    __tablename__ = "neo_nodes"
    __table_args__ = (
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="ck_neo_nodes_confidence_range"),
        Index("ix_neo_nodes_agent_domain", "agent_id", "domain"),
        Index("ix_neo_nodes_agent_type", "agent_id", "node_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    agent_id: Mapped[str] = mapped_column(ForeignKey("neo_agents.id", ondelete="CASCADE"), index=True)
    node_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(default=0.5)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("neo_nodes.id", ondelete="SET NULL"), index=True, nullable=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("neo_sources.id", ondelete="SET NULL"), nullable=True)
    spark_id: Mapped[str | None] = mapped_column(ForeignKey("neo_sparks.id", ondelete="SET NULL"), nullable=True)
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    consolidation_version: Mapped[int] = mapped_column(default=0)
    last_consolidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active", index=True)

    agent: Mapped["NeoAgent"] = relationship(back_populates="nodes")
    parent: Mapped["NeoNode | None"] = relationship(remote_side="NeoNode.id", back_populates="children")
    children: Mapped[list["NeoNode"]] = relationship(back_populates="parent")
    source: Mapped["NeoSource | None"] = relationship(back_populates="nodes")
    spark: Mapped["NeoSpark | None"] = relationship(foreign_keys=[spark_id], back_populates="resolved_nodes")
    outgoing_edges: Mapped[list["NeoEdge"]] = relationship(
        foreign_keys="NeoEdge.from_node_id",
        back_populates="from_node",
    )
    incoming_edges: Mapped[list["NeoEdge"]] = relationship(
        foreign_keys="NeoEdge.to_node_id",
        back_populates="to_node",
    )


class NeoEdge(TimestampMixin, Base):
    __tablename__ = "neo_edges"
    __table_args__ = (
        CheckConstraint("weight >= 0.0 AND weight <= 1.0", name="ck_neo_edges_weight_range"),
        UniqueConstraint("from_node_id", "to_node_id", "edge_type", name="uq_neo_edges_unique_triplet"),
        Index("ix_neo_edges_from_to", "from_node_id", "to_node_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    agent_id: Mapped[str] = mapped_column(ForeignKey("neo_agents.id", ondelete="CASCADE"), index=True)
    from_node_id: Mapped[str] = mapped_column(ForeignKey("neo_nodes.id", ondelete="CASCADE"), index=True)
    to_node_id: Mapped[str] = mapped_column(ForeignKey("neo_nodes.id", ondelete="CASCADE"), index=True)
    edge_type: Mapped[str] = mapped_column(String(32), index=True)
    weight: Mapped[float] = mapped_column(default=0.5)
    description: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("neo_sources.id", ondelete="SET NULL"), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)

    agent: Mapped["NeoAgent"] = relationship(back_populates="edges")
    from_node: Mapped["NeoNode"] = relationship(foreign_keys=[from_node_id], back_populates="outgoing_edges")
    to_node: Mapped["NeoNode"] = relationship(foreign_keys=[to_node_id], back_populates="incoming_edges")
    source: Mapped["NeoSource | None"] = relationship(back_populates="edges")


class NeoSpark(Base):
    __tablename__ = "neo_sparks"
    __table_args__ = (
        CheckConstraint("priority >= 0.0 AND priority <= 1.0", name="ck_neo_sparks_priority_range"),
        Index("ix_neo_sparks_agent_status", "agent_id", "status"),
        Index("ix_neo_sparks_agent_priority", "agent_id", "priority"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    agent_id: Mapped[str] = mapped_column(ForeignKey("neo_agents.id", ondelete="CASCADE"), index=True)
    spark_type: Mapped[str] = mapped_column(String(32), index=True)
    target_node_id: Mapped[str | None] = mapped_column(ForeignKey("neo_nodes.id", ondelete="SET NULL"), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    priority: Mapped[float] = mapped_column(default=0.5)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("neo_sources.id", ondelete="SET NULL"), nullable=True)
    resolved_node_id: Mapped[str | None] = mapped_column(ForeignKey("neo_nodes.id", ondelete="SET NULL"), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent: Mapped["NeoAgent"] = relationship(back_populates="sparks", foreign_keys=[agent_id])
    target_node: Mapped["NeoNode | None"] = relationship(foreign_keys=[target_node_id])
    resolved_node: Mapped["NeoNode | None"] = relationship(foreign_keys=[resolved_node_id])
    source: Mapped["NeoSource | None"] = relationship(back_populates="sparks")
    resolved_nodes: Mapped[list["NeoNode"]] = relationship(
        foreign_keys="NeoNode.spark_id",
        back_populates="spark",
    )
