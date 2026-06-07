from __future__ import annotations

import asyncio

import pytest

from neo.core.resolution_scheduler import ResolutionScheduler


class FakeStore:
    def __init__(self):
        self.agent = {"id": "agent-1", "name": "neo"}
        self.sparks = [
            {"id": "spark-1", "priority": 1.0},
            {"id": "spark-2", "priority": 0.9},
        ]

    async def get_agent(self, agent_id: str):
        return self.agent

    async def get_active_sparks_for_resolution(self, agent_id: str, *, limit: int, min_priority: float):
        return self.sparks[:limit]


class FakeAPI:
    def __init__(self):
        self.store = FakeStore()


class SlowResolver:
    def __init__(self):
        self.calls: list[str] = []

    async def resolve(self, spark, agent, mode: str, trigger: str):
        self.calls.append(spark["id"])
        await asyncio.sleep(2)
        return {"success": True, "spark_id": spark["id"]}


@pytest.mark.asyncio
async def test_resolution_scheduler_timeboxes_background_batch():
    resolver = SlowResolver()
    scheduler = ResolutionScheduler(
        FakeAPI(),
        resolver,
        agent_id="agent-1",
        batch_size=2,
        max_runtime_seconds=1,
    )

    await scheduler._tick()

    assert resolver.calls == ["spark-1"]
