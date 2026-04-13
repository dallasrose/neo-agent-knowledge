import pytest
from sqlalchemy.exc import IntegrityError

from neo.models import NeoAgent, NeoEdge, NeoNode, NeoSource, NeoSpark


@pytest.mark.asyncio
async def test_create_all_entities_and_relationships(session):
    agent = NeoAgent(name="neo", specialty="semantic memory")
    session.add(agent)
    await session.flush()

    source = NeoSource(
        agent_id=agent.id,
        source_type="document",
        title="Spec",
        reference="/tmp/spec.md",
    )
    session.add(source)
    await session.flush()

    spark = NeoSpark(
        agent_id=agent.id,
        spark_type="open_question",
        description="Need grounding data",
        priority=0.7,
        domain="memory",
    )
    session.add(spark)
    await session.flush()

    parent = NeoNode(
        agent_id=agent.id,
        node_type="concept",
        title="Semantic Memory",
        content="Root concept",
        summary="semantic memory = structured knowledge",
        confidence=0.9,
        domain="memory",
        source_id=source.id,
    )
    session.add(parent)
    await session.flush()

    node = NeoNode(
        agent_id=agent.id,
        node_type="finding",
        title="Neo stores knowledge",
        content="Detailed finding",
        summary="neo stores structured knowledge",
        confidence=0.8,
        parent_id=parent.id,
        source_id=source.id,
        spark_id=spark.id,
        domain="memory",
    )
    session.add(node)
    await session.flush()

    edge = NeoEdge(
        agent_id=agent.id,
        from_node_id=parent.id,
        to_node_id=node.id,
        edge_type="supports",
        weight=0.75,
        description="Parent concept supports finding",
        source_id=source.id,
    )
    session.add(edge)
    await session.commit()

    await session.refresh(agent, ["nodes", "sources", "sparks"])
    await session.refresh(source, ["nodes"])
    await session.refresh(node, ["parent", "source", "spark", "incoming_edges"])

    assert agent.nodes[0].title == "Semantic Memory"
    assert source.nodes[0].title == "Semantic Memory"
    assert node.parent.title == "Semantic Memory"
    assert node.source.title == "Spec"
    assert node.spark.description == "Need grounding data"
    assert node.incoming_edges[0].description == "Parent concept supports finding"


@pytest.mark.asyncio
async def test_confidence_constraint_validation(session):
    agent = NeoAgent(name="neo", specialty="semantic memory")
    session.add(agent)
    await session.flush()

    node = NeoNode(
        agent_id=agent.id,
        node_type="finding",
        title="Bad confidence",
        content="x",
        summary="x",
        confidence=1.5,
    )
    session.add(node)

    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_weight_constraint_validation(session):
    agent = NeoAgent(name="neo", specialty="semantic memory")
    session.add(agent)
    await session.flush()

    left = NeoNode(
        agent_id=agent.id,
        node_type="concept",
        title="A",
        content="A",
        summary="A",
        confidence=0.5,
    )
    right = NeoNode(
        agent_id=agent.id,
        node_type="concept",
        title="B",
        content="B",
        summary="B",
        confidence=0.5,
    )
    session.add_all([left, right])
    await session.flush()

    edge = NeoEdge(
        agent_id=agent.id,
        from_node_id=left.id,
        to_node_id=right.id,
        edge_type="connects",
        weight=1.2,
        description="invalid",
    )
    session.add(edge)

    with pytest.raises(IntegrityError):
        await session.commit()
