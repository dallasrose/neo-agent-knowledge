from __future__ import annotations

import pytest

from neo.core.scheduler import ConsolidationScheduler


class StubEngine:
    def __init__(self) -> None:
        self.runs: list[str] = []

    async def run(self, agent_id: str) -> None:
        self.runs.append(agent_id)


@pytest.mark.asyncio
async def test_consolidation_scheduler_runs_after_hook_after_consolidation():
    calls: list[str] = []
    engine = StubEngine()

    async def after_consolidation() -> None:
        calls.append("contemplate")

    scheduler = ConsolidationScheduler(
        store=None,
        engine=engine,
        agent_id="agent-1",
        schedule="0 */6 * * *",
        node_threshold=20,
        after_consolidation=after_consolidation,
    )

    await scheduler._run_consolidation()

    assert engine.runs == ["agent-1"]
    assert calls == ["contemplate"]
