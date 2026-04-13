from neo.store.sqlite import SQLiteStore


class PostgresStore(SQLiteStore):
    """Placeholder Postgres store.

    The v1 implementation keeps the contract aligned with SQLite while the
    pgvector-backed specialization is completed.
    """
