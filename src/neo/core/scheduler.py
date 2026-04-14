from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable

from croniter import croniter

from neo.core.consolidation import ConsolidationEngine
from neo.store.interface import StoreInterface


class ConsolidationScheduler:
    def __init__(
        self,
        store: StoreInterface,
        engine: ConsolidationEngine,
        *,
        agent_id: str,
        schedule: str,
        node_threshold: int,
        poll_interval_seconds: float = 1.0,
        after_consolidation: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.store = store
        self.engine = engine
        self.agent_id = agent_id
        self.schedule = schedule
        self.node_threshold = node_threshold
        self.poll_interval_seconds = poll_interval_seconds
        self.after_consolidation = after_consolidation
        self._task: asyncio.Task[None] | None = None
        self._running = False

    def start(self) -> asyncio.Task[None]:
        self._running = True
        self._task = asyncio.create_task(self.run())
        return self._task

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def run(self) -> None:
        iterator = croniter(self.schedule, datetime.now(timezone.utc))
        next_run = iterator.get_next(datetime)
        while self._running:
            now = datetime.now(timezone.utc)
            if now >= next_run:
                await self._run_consolidation()
                next_run = iterator.get_next(datetime)
            count = await self.store.count_nodes_since(self.agent_id, now.replace(hour=0, minute=0, second=0, microsecond=0))
            if count >= self.node_threshold:
                await self._run_consolidation()
                next_run = iterator.get_next(datetime)
            await asyncio.sleep(self.poll_interval_seconds)

    async def _run_consolidation(self) -> None:
        await self.engine.run(self.agent_id)
        if self.after_consolidation is not None:
            await self.after_consolidation()
