from __future__ import annotations

from typing import Any

from neo.integrations.hermes.config import HermesNeoConfig

_TYPE_WEIGHTS = {
    "synthesis": 0.20,
    "theory": 0.16,
    "finding": 0.14,
    "concept": 0.10,
    "answer": 0.08,
    "question": 0.04,
    "idea": 0.02,
}


def _node_score(node: dict[str, Any]) -> float:
    similarity = float(node.get("similarity") or 0.0)
    # Assembled graph-neighborhood nodes may not be seed hits. Give them a tiny
    # chance through confidence/type, but don't pretend they were direct matches.
    confidence = float(node.get("confidence") or 0.0)
    node_type = str(node.get("node_type") or "").lower()
    type_weight = _TYPE_WEIGHTS.get(node_type, 0.0)
    return (similarity * 0.60) + (confidence * 0.30) + type_weight


def build_signals(result: dict[str, Any], config: HermesNeoConfig) -> list[dict[str, Any]]:
    """Convert Neo search results into gated, ranked semantic recall signals."""

    signals: list[dict[str, Any]] = []
    for node in result.get("nodes") or []:
        confidence = float(node.get("confidence") or 0.0)
        if confidence < config.min_confidence:
            continue
        score = _node_score(node)
        if score < config.signal_threshold:
            continue
        signals.append(
            {
                "id": node.get("id"),
                "title": node.get("title") or "Untitled",
                "node_type": node.get("node_type") or "unknown",
                "confidence": confidence,
                "similarity": float(node.get("similarity") or 0.0),
                "score": score,
                "summary": node.get("summary") or "",
                "why": node.get("summary") or "Neo found semantically related durable knowledge.",
                "domain": node.get("domain"),
            }
        )
    signals.sort(key=lambda signal: signal["score"], reverse=True)
    return signals[: config.max_signals]
