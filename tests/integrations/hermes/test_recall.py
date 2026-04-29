from neo.integrations.hermes.config import HermesNeoConfig
from neo.integrations.hermes.recall import build_signals


def test_build_signals_filters_below_threshold():
    config = HermesNeoConfig(signal_threshold=0.6)
    result = {
        "nodes": [
            {
                "title": "Weak",
                "confidence": 0.2,
                "similarity": 0.3,
                "node_type": "idea",
                "summary": "weak match",
            }
        ]
    }
    assert build_signals(result, config) == []


def test_build_signals_prioritizes_synthesis():
    config = HermesNeoConfig(signal_threshold=0.1)
    result = {
        "nodes": [
            {
                "title": "Raw Idea",
                "confidence": 0.9,
                "similarity": 0.5,
                "node_type": "idea",
                "summary": "x",
            },
            {
                "title": "Synthesis",
                "confidence": 0.7,
                "similarity": 0.5,
                "node_type": "synthesis",
                "summary": "y",
            },
        ]
    }
    signals = build_signals(result, config)
    assert signals[0]["title"] == "Synthesis"
    assert signals[0]["why"] == "y"


def test_build_signals_respects_max_signals():
    config = HermesNeoConfig(signal_threshold=0.1, max_signals=2)
    result = {
        "nodes": [
            {"title": f"Node {i}", "confidence": 1, "similarity": 1, "node_type": "finding", "summary": "x"}
            for i in range(4)
        ]
    }
    assert len(build_signals(result, config)) == 2
