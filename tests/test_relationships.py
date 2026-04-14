from __future__ import annotations

from neo.core.relationships import _parse_decision


def test_parse_relationship_decision_accepts_specific_edge_type():
    decision = _parse_decision(
        '```json\n{"edge_type":"extends","description":"Adds a deployment implication","confidence":0.83}\n```'
    )

    assert decision.edge_type == "extends"
    assert decision.description == "Adds a deployment implication"
    assert decision.confidence == 0.83


def test_parse_relationship_decision_rejects_unknown_edge_type():
    decision = _parse_decision('{"edge_type":"similar_to","description":"too vague","confidence":0.9}')

    assert decision.edge_type is None
    assert decision.confidence == 0.9
