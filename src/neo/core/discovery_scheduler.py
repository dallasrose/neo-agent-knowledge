"""Discovery scheduler — runs DiscoveryJob on a configurable interval."""
from __future__ import annotations
import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class DiscoveryScheduler:
    def __init__(
        self,
        api: Any,
        job: Any,
        agent_id: str,
        interval_minutes: int = 60,
        batch_size: int = 5,
    ) -> None:
        self.api = api
        self.job = job
        self.agent_id = agent_id
        self.interval = interval_minutes * 60
        self.batch_size = batch_size
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
        logger.info(
            "Discovery scheduler: every %dm, batch=%d", self.interval // 60, self.batch_size
        )
        # Run once at startup (after a short delay so the server is fully up)
        await asyncio.sleep(10)
        await self._tick()
        # Then on the configured interval
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self._tick()
            except Exception as exc:
                logger.warning("Discovery scheduler error: %s", exc)

    async def _tick(self) -> None:
        from neo.config import settings
        agent = await self.api.store.get_agent(self.agent_id)
        if agent is None:
            return
        # Run if there are configured sources OR if the agent has a specialty
        # (autonomous search mode doesn't need explicit sources)
        sources  = (agent.get("config") or {}).get("research_sources") or []
        specialty = (agent.get("specialty") or "").strip()
        if not sources and not specialty:
            return
        result = await self.job.run(
            agent,
            batch_size=self.batch_size,
            lookback_days=settings.discovery_lookback_days,
        )
        if result["ingested"] > 0:
            logger.info(
                "Discovery: ingested %d new item(s) (sources=%d, autonomous=%s)",
                result["ingested"], result["sources_checked"],
                "yes" if result.get("autonomous_queries") else "no",
            )
