from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from neo.config import settings
from neo.core.consolidation import ConsolidationEngine
from neo.core.discovery import DiscoveryJob
from neo.core.discovery_scheduler import DiscoveryScheduler
from neo.core.scheduler import ConsolidationScheduler
from neo.db import close_db, init_db
from neo.rest.routes import router
from neo.runtime import ensure_default_agent, get_api_singleton, reset_runtime_singletons

# Static files live inside the Python package at src/neo/static/
# This path works both in development and in installed packages.
_FRONTEND_DIST = Path(__file__).parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    api = get_api_singleton()
    app.state.neo_api = api
    agent = await ensure_default_agent(api)

    scheduler = None
    if settings.consolidation_enabled:
        scheduler = ConsolidationScheduler(
            api.store,
            ConsolidationEngine(api.store),
            agent_id=agent["id"],
            schedule=settings.consolidation_schedule,
            node_threshold=settings.consolidation_node_threshold,
            poll_interval_seconds=settings.scheduler_poll_interval_seconds,
        )
        scheduler.start()
    app.state.neo_scheduler = scheduler

    discovery_sched = None
    if settings.discovery_enabled:
        from neo.core.youtube import YouTubeSearchClient, EchoSearchAsYouTube
        from neo.core.web_search import WebSearchClient

        yt_search = None
        if settings.youtube_api_key:
            yt_search = YouTubeSearchClient(settings.youtube_api_key)
        elif settings.search_api_key:
            yt_search = EchoSearchAsYouTube(
                WebSearchClient(settings.search_provider, settings.search_api_key)
            )

        res_key = settings.llm_api_key_for("resolution")
        res_model = settings.llm_model_for("resolution")
        res_url = settings.llm_base_url_for("resolution")
        res_provider = settings.llm_provider_for("resolution")
        discovery_llm = None
        if settings.llm_configured_for("resolution"):
            from neo.core.resolver import ResolutionLLM
            discovery_llm = ResolutionLLM(
                api_key=res_key,
                model=res_model,
                base_url=res_url,
                provider=res_provider,
            )

        discovery_sched = DiscoveryScheduler(
            api,
            DiscoveryJob(api, llm=discovery_llm, yt_search=yt_search),
            agent_id=agent["id"],
            interval_minutes=settings.discovery_interval_minutes,
            batch_size=settings.discovery_batch_size,
        )
        discovery_sched.start()
    app.state.neo_discovery = discovery_sched

    try:
        yield
    finally:
        if scheduler is not None:
            await scheduler.stop()
        if discovery_sched is not None:
            await discovery_sched.stop()
        await close_db()
        reset_runtime_singletons()


def create_app() -> FastAPI:
    app = FastAPI(title="Neo REST API", lifespan=lifespan)
    app.include_router(router)

    @app.exception_handler(ValueError)
    async def handle_value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(RuntimeError)
    async def handle_runtime_error(_: Request, exc: RuntimeError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    # Serve the NeoVis frontend if it has been built
    if _FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

        @app.get("/favicon.svg", include_in_schema=False)
        async def serve_favicon() -> FileResponse:
            return FileResponse(_FRONTEND_DIST / "favicon.svg")

        @app.get("/icons.svg", include_in_schema=False)
        async def serve_icons() -> FileResponse:
            return FileResponse(_FRONTEND_DIST / "icons.svg")

        @app.get("/", include_in_schema=False)
        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_frontend(full_path: str = "") -> FileResponse:
            # Don't intercept API routes
            if full_path.startswith("api/"):
                raise ValueError("Not found")
            return FileResponse(_FRONTEND_DIST / "index.html")

    return app


app = create_app()
