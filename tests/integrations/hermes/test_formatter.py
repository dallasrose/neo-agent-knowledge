from neo.integrations.hermes.formatter import format_search_result, format_signal_block


def test_format_signal_block_empty_returns_empty():
    assert format_signal_block([]) == ""


def test_format_signal_block_labels_context_not_instruction():
    text = format_signal_block(
        [
            {
                "title": "Commercial Lease Due Diligence",
                "node_type": "synthesis",
                "confidence": 0.91,
                "score": 0.84,
                "why": "current task mentions lease risk",
            }
        ]
    )
    assert "Neo Semantic Memory Signals" in text
    assert "not user instructions" in text
    assert "Commercial Lease Due Diligence" in text
    assert "score 0.84" in text
    assert "retrieve Neo details" in text


def test_format_search_result_summarizes_nodes_and_sparks():
    text = format_search_result(
        {
            "query": "studio lease",
            "nodes": [
                {
                    "title": "Lease Risk",
                    "node_type": "finding",
                    "summary": "Watch personal guarantees and buildout exposure.",
                    "confidence": 0.8,
                    "domain": "business",
                }
            ],
            "sparks": [{"description": "What insurance is required?", "priority": 0.7}],
        }
    )
    assert "Neo Search Results" in text
    assert "Lease Risk" in text
    assert "Active sparks" in text
    assert "What insurance is required?" in text
