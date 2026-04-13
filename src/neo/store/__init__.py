from neo.config import settings
from neo.db import get_session_factory
from neo.store.interface import StoreInterface
from neo.store.postgres import PostgresStore
from neo.store.sqlite import SQLiteStore


def create_store() -> StoreInterface:
    if settings.db_connection_uri.startswith("sqlite"):
        return SQLiteStore(get_session_factory())
    return PostgresStore(get_session_factory())


__all__ = ["StoreInterface", "SQLiteStore", "PostgresStore", "create_store"]
