from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


async def run_contemplation_pass(api: Any, agent_id: str, *, batch: int) -> int:
    """Scan candidate nodes once and generate sparks where useful."""
    agent = await api.store.get_agent(agent_id)
    if agent is None:
        return 0

    core_domains: set[str] = set(agent.get("domains") or [])
    recent_cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    recent_all = await api.store.get_nodes_by_agent(agent_id, since=recent_cutoff, limit=batch * 2)
    recent_on_topic = [
        node for node in recent_all
        if node.get("domain") and node["domain"] in core_domains
    ]
    recent_other = [
        node for node in recent_all
        if node not in recent_on_topic
    ]
    isolated = await api.store.get_nodes_without_sparks(agent_id, limit=batch)

    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for node in [*recent_on_topic, *recent_other, *isolated]:
        if node["id"] not in seen and len(candidates) < batch:
            seen.add(node["id"])
            candidates.append(node)

    if candidates:
        on_topic_count = sum(
            1 for node in candidates
            if node.get("domain") and node["domain"] in core_domains
        )
        logger.info(
            "Contemplation: %d candidates (%d on-topic, %d other)",
            len(candidates), on_topic_count, len(candidates) - on_topic_count,
        )

    sparked = 0
    for node in candidates:
        try:
            sparks = await api.spark_generator.generate_for_node(agent=agent, node=node)
            sparked += len(sparks or [])
        except Exception as exc:
            logger.warning("Contemplation failed for node %s: %s", node.get("id"), exc)
    return sparked
