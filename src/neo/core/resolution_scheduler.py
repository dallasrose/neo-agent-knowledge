"""Resolution scheduler — runs SparkResolver on an interval."""
from __future__ import annotations
import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ResolutionScheduler:
    def __init__(
        self,
        api: Any,
        resolver: Any,
        agent_id: str,
        interval_minutes: int = 30,
        batch_size: int = 3,
        min_priority: float = 0.5,
    ) -> None:
        self.api = api
        self.resolver = resolver
        self.agent_id = agent_id
        self.interval = interval_minutes * 60
        self.batch_size = batch_size
        self.min_priority = min_priority
        self._task: asyncio.Task | None = None

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self._run())
        return self._task

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        logger.info("Resolution scheduler: every %dm, batch=%d", self.interval // 60, self.batch_size)
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self._tick()
            except Exception as e:
                logger.warning("Resolution scheduler error: %s", e)

    async def _tick(self) -> None:
        agent = await self.api.store.get_agent(self.agent_id)
        if not agent:
            return
        sparks = await self.api.store.get_active_sparks_for_resolution(
            self.agent_id, limit=self.batch_size, min_priority=self.min_priority
        )
        if not sparks:
            return
        logger.info("Resolution: %d sparks to process", len(sparks))
        for spark in sparks:
            try:
                result = await self.resolver.resolve(spark, agent, mode="apply", trigger="background")
                status = "resolved" if result.get("success") else "failed"
                logger.info("Resolution: spark %s %s", result.get("spark_id"), status)
            except Exception as e:
                logger.warning("Resolution: spark %s error: %s", spark.get("id"), e)
