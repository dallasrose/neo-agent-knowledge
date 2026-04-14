from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from neo.enums import EdgeType

logger = logging.getLogger(__name__)

_EDGE_TYPES = {item.value for item in EdgeType}


@dataclass(frozen=True)
class RelationshipDecision:
    edge_type: str | None
    description: str
    confidence: float
    source: str = "heuristic"


class RelationshipJudge(Protocol):
    async def judge(
        self,
        source: dict[str, Any],
        candidate: dict[str, Any],
        similarity: float,
    ) -> RelationshipDecision:
        ...


class HeuristicRelationshipJudge:
    async def judge(
        self,
        source: dict[str, Any],
        candidate: dict[str, Any],
        similarity: float,
    ) -> RelationshipDecision:
        if similarity < 0.82:
            return RelationshipDecision(None, "", 0.0)
        return RelationshipDecision(
            EdgeType.CONNECTS.value,
            "Semantically related knowledge",
            min(similarity, 1.0),
            "heuristic",
        )


class LLMRelationshipJudge:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str | None = None,
        provider: str | None = None,
        fallback: RelationshipJudge | None = None,
    ) -> None:
        from neo.core.llm import NeoLLMClient

        self._client = NeoLLMClient(
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider=provider,
        )
        self._fallback = fallback or HeuristicRelationshipJudge()

    async def judge(
        self,
        source: dict[str, Any],
        candidate: dict[str, Any],
        similarity: float,
    ) -> RelationshipDecision:
        prompt = f"""Classify a possible typed edge in a durable knowledge graph.

Source node:
- id: {source.get("id")}
- type: {source.get("node_type")}
- title: {source.get("title")}
- summary: {source.get("summary")}
- content: {(source.get("content") or "")[:900]}

Candidate node:
- id: {candidate.get("id")}
- type: {candidate.get("node_type")}
- title: {candidate.get("title")}
- summary: {candidate.get("summary")}
- content: {(candidate.get("content") or "")[:900]}

Embedding similarity: {similarity:.3f}

Allowed edge_type values:
supports, contradicts, prerequisite_for, extends, example_of, questions, resolves, inspired, connects

Rules:
- Return no edge when the relationship is vague, topical only, redundant, or not useful for retrieval.
- Use connects only when no more specific allowed type is accurate.
- Use contradicts only for a direct conflict about the same specific claim.
- Use supports when one node provides evidence for the other.
- Use extends when one node adds a more specific implication, mechanism, or follow-on claim.
- Use example_of when one node is a concrete example of the other.
- Use prerequisite_for when understanding one is needed before the other.
- Direction matters: classify the edge from Source node to Candidate node.

Respond with one JSON object only:
{{"edge_type": "supports|contradicts|prerequisite_for|extends|example_of|questions|resolves|inspired|connects|null", "description": "short reason", "confidence": 0.0-1.0}}"""
        try:
            raw = await asyncio.wait_for(self._client.call(prompt, max_tokens=2048), timeout=70.0)
            decision = _parse_decision(raw)
            if decision.edge_type is None or decision.confidence < 0.7:
                return RelationshipDecision(None, "", decision.confidence)
            return RelationshipDecision(decision.edge_type, decision.description, decision.confidence, "llm")
        except Exception as exc:
            logger.warning("Relationship judge failed, using heuristic fallback: %s", exc)
            return await self._fallback.judge(source, candidate, similarity)


def _parse_decision(raw: str) -> RelationshipDecision:
    text = (raw or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.strip().lower().startswith("json"):
            text = text.strip()[4:]
    start = text.find("{")
    end = text.rfind("}") + 1
    data = json.loads(text[start:end])
    edge_type_raw = data.get("edge_type")
    edge_type = str(edge_type_raw).strip() if edge_type_raw is not None else ""
    if edge_type.lower() in {"", "none", "null"}:
        edge_type = None
    elif edge_type not in _EDGE_TYPES:
        edge_type = None
    description = str(data.get("description") or "").strip()[:240]
    confidence = float(data.get("confidence") or 0.0)
    confidence = max(0.0, min(confidence, 1.0))
    return RelationshipDecision(edge_type, description, confidence, "llm")
