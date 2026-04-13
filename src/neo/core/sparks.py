from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from neo.store.interface import StoreInterface

logger = logging.getLogger(__name__)


class SparkLLM(Protocol):
    async def generate(self, node: dict[str, Any], context: list[dict[str, Any]], agent_focus: str = "") -> list[dict[str, Any]]:
        ...


class NullSparkLLM:
    async def generate(self, node: dict[str, Any], context: list[dict[str, Any]], agent_focus: str = "") -> list[dict[str, Any]]:
        return []


class AnthropicSparkLLM:
    """Real spark generator — works with Anthropic or any Anthropic-compatible endpoint (e.g. MiniMax)."""

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5", base_url: str | None = None) -> None:
        import anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url)
        self._model = model

    async def generate(self, node: dict[str, Any], context: list[dict[str, Any]], agent_focus: str = "") -> list[dict[str, Any]]:
        node_type = node.get("node_type", "")
        # Structural/policy nodes: contradictions are almost always false positives
        # because conditional logic ("use X except when Y") looks like X vs ¬X.
        # Only open_question and weak_edge are appropriate for these types.
        structural_types = {"concept", "instruction", "policy", "procedure", "synthesis"}
        allowed_spark_types = (
            "open_question, weak_edge"
            if node_type in structural_types
            else "open_question, contradiction, weak_edge"
        )

        context_text = "\n".join(
            f"- [{n.get('node_type', 'node')}] {n.get('title', '')}: {(n.get('summary') or n.get('content', ''))[:400]}"
            for n in context[:5]
        ) or "None"

        contradiction_guidance = "" if node_type in structural_types else """
CONTRADICTION rules — high bar, often wrong:
- Only valid when two nodes make directly opposing claims about the same specific fact or behavior.
- Conditional logic ("use X except when Y") is NOT a contradiction — it is intentional scope separation.
- Before emitting a contradiction: re-read both the node content and related knowledge above. If the node itself already explains why both things coexist, it is not a contradiction — return [] for that spark.
- When in doubt, prefer open_question over contradiction.
"""

        focus_line = f"\nAgent research direction: {agent_focus}\n" if agent_focus else ""

        prompt = f"""A knowledge node was added to a semantic graph:

Title: {node.get('title')}
Type: {node_type}
Domain: {node.get('domain') or 'unspecified'}
Content: {(node.get('content') or '')[:800]}

Related nodes already in the graph:
{context_text}
{focus_line}{contradiction_guidance}
Generate 0-3 research sparks that represent genuine gaps, unanswered questions, or missing connections this node raises. Bias toward [] — only emit a spark if it would be worth an agent's time to investigate. When the agent has a research direction, prefer sparks that advance it.

Allowed spark types for this node: {allowed_spark_types}

Respond with a JSON array only. Each item: {{"spark_type": "...", "description": "...(max 120 chars)"}}
Return [] if no meaningful sparks exist."""

        try:
            response = await asyncio.wait_for(
                self._client.messages.create(
                    model=self._model,
                    max_tokens=2048,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=60.0,
            )
            text_block = next((b for b in response.content if hasattr(b, "text")), None)
            if text_block is None:
                return []
            raw = text_block.text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```", 2)[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            # Find the JSON array — tolerant of surrounding prose
            start = raw.find("[")
            end = raw.rfind("]")
            if start == -1:
                return []
            if end == -1:
                # Truncated — try to close it ourselves
                raw = raw[start:] + "]"
            else:
                raw = raw[start : end + 1]
            proposals = json.loads(raw)
            if not isinstance(proposals, list):
                return []
            valid = [p for p in proposals if isinstance(p, dict) and "spark_type" in p and "description" in p]
            return valid
        except Exception as exc:
            logger.warning("AnthropicSparkLLM.generate failed: %s", exc)
            return []


class SparkGenerator:
    BASE_PRIORITIES = {
        "contradiction": 0.9,
        "isolated_node": 0.8,
        "open_question": 0.7,
        "weak_edge": 0.6,
        "thin_domain": 0.4,
    }

    def __init__(self, store: StoreInterface, llm: SparkLLM | None = None) -> None:
        self.store = store
        self.llm = llm or NullSparkLLM()

    def score_priority(
        self,
        spark_type: str,
        *,
        in_core_domain: bool = False,
        is_recent: bool = False,
        edge_count: int = 0,
    ) -> float:
        priority = self.BASE_PRIORITIES[spark_type]
        if in_core_domain:
            priority += 0.1
        if is_recent:
            priority += 0.05
        if edge_count <= 1:
            priority += 0.1
        if edge_count >= 10:
            priority -= 0.1
        return round(max(0.0, min(1.0, priority)), 4)

    async def generate_for_node(
        self,
        *,
        agent: dict[str, Any],
        node: dict[str, Any],
        max_sparks_per_node: int = 3,
        max_sparks_per_day: int = 20,
    ) -> list[dict[str, Any]]:
        active_today = await self.store.get_sparks(agent["id"], status="active", limit=max_sparks_per_day)
        today_floor = datetime.now(timezone.utc) - timedelta(days=1)
        today_count = len([
            spark for spark in active_today
            if (spark["created_at"].replace(tzinfo=timezone.utc) if spark["created_at"].tzinfo is None else spark["created_at"]) >= today_floor
        ])
        remaining_budget = max(0, min(max_sparks_per_node, max_sparks_per_day - today_count))
        if remaining_budget == 0:
            return []

        context = await self.store.vector_search(
            agent["id"],
            node.get("embedding") or [],
            top_k=5,
            domain=node.get("domain"),
        )

        # Build agent focus string from specialty + domains so the LLM can
        # bias spark generation toward the agent's research direction.
        specialty = (agent.get("specialty") or "").strip()
        domains = agent.get("domains") or []
        domain_str = ", ".join(domains) if domains else ""
        agent_focus = specialty or domain_str

        proposals = await self.llm.generate(node, context, agent_focus=agent_focus)

        created: list[dict[str, Any]] = []
        edge_count = len(await self.store.get_edges(node["id"]))
        created_at = node["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        is_recent = created_at >= datetime.now(timezone.utc) - timedelta(days=1)
        core_domains = set(agent.get("domains") or [])
        in_core_domain = bool(node.get("domain") and node["domain"] in core_domains)

        for proposal in proposals[:remaining_budget]:
            spark_type = proposal["spark_type"]
            priority = proposal.get(
                "priority",
                self.score_priority(
                    spark_type,
                    in_core_domain=in_core_domain,
                    is_recent=is_recent,
                    edge_count=edge_count,
                ),
            )
            created.append(
                await self.store.create_spark(
                    agent["id"],
                    spark_type,
                    proposal["description"],
                    priority=priority,
                    domain=node.get("domain"),
                    target_node_id=node["id"],
                    source_id=node.get("source_id"),
                    metadata={"generated_from": "ingestion"},
                )
            )
        return created

    async def generate_on_consolidation(
        self,
        agent: dict[str, Any],
        findings: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        for finding in findings:
            created.append(
                await self.store.create_spark(
                    agent["id"],
                    finding["spark_type"],
                    finding["description"],
                    priority=finding.get("priority", self.BASE_PRIORITIES.get(finding["spark_type"], 0.5)),
                    domain=finding.get("domain"),
                    target_node_id=finding.get("target_node_id"),
                    source_id=finding.get("source_id"),
                    metadata={"generated_from": "consolidation"},
                )
            )
        return created
