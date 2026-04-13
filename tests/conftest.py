import asyncio
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

# Disable background jobs before any Neo module is imported.
# The DB URI is handled per-test via the autouse fixture below.
os.environ.setdefault("NEO_CONSOLIDATION_ENABLED", "false")
os.environ.setdefault("NEO_CONTEMPLATION_ENABLED", "false")
os.environ.setdefault("NEO_EMBEDDING_PROVIDER", "mock")

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from neo.db import Base


@pytest.fixture(scope="session")
def event_loop() -> AsyncIterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db_session:
        yield db_session

    await engine.dispose()


@pytest_asyncio.fixture()
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    await engine.dispose()


@pytest.fixture(autouse=True)
def reset_neo_singletons(tmp_path):
    """Give every test an isolated Neo DB and reset all runtime singletons.

    REST tests (which spin up a full FastAPI app via TestClient) use a temp
    SQLite file so they get a clean slate without pool-sharing issues.
    Unit tests that create their own session_factory are unaffected.
    """
    import neo.config as config_module
    import neo.db as db_module
    from neo.runtime import reset_runtime_singletons

    # Point the app at a per-test temp file DB so REST tests don't share state.
    original_uri = config_module.settings.db_connection_uri
    test_db = tmp_path / "neo_test.db"
    config_module.settings.db_connection_uri = f"sqlite+aiosqlite:///{test_db}"

    # Clear db-level caches so a fresh engine is built for the new URI.
    db_module.get_engine.cache_clear()
    db_module.get_session_factory.cache_clear()
    reset_runtime_singletons()
    yield
    db_module.get_engine.cache_clear()
    db_module.get_session_factory.cache_clear()
    reset_runtime_singletons()

    # Restore original URI so the setting doesn't leak into the next test
    config_module.settings.db_connection_uri = original_uri


class MockEmbeddingClient:
    def embed_text(self, text: str) -> list[float]:
        tokens = [float((ord(char) % 17) / 17) for char in text[:8]]
        return tokens + [0.0] * (8 - len(tokens))


@pytest.fixture()
def mock_embedding_client() -> MockEmbeddingClient:
    return MockEmbeddingClient()
