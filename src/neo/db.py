from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from neo.config import settings


class Base(DeclarativeBase):
    pass


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    connect_args: dict[str, object] = {}
    if settings.db_connection_uri.startswith("sqlite"):
        connect_args = {"timeout": 30}

    runtime_engine = create_async_engine(
        settings.db_connection_uri,
        echo=settings.db_sql_debug,
        future=True,
        connect_args=connect_args,
    )
    if settings.db_connection_uri.startswith("sqlite"):
        @event.listens_for(runtime_engine.sync_engine, "connect")
        def configure_sqlite(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return runtime_engine


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)


engine: AsyncEngine = get_engine()
SessionLocal = get_session_factory()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    from neo import models  # noqa: F401

    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    await get_engine().dispose()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
