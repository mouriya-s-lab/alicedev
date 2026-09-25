"""Single-writer DuckDB store.

DuckDB's Python connection is not safe for concurrent use, so every query runs
on a dedicated single-thread executor serialized by an internal connection lock.
``Store.lock`` is a *separate* asyncio.Lock repositories take for check-then-write
sequences that must be atomic across several queries.

``open()`` applies schema v4. A v2 database (``sessions.session_ref`` exists) is
migrated once, in one transaction, by :mod:`alicedev.store.migrate_v3`; v3 databases
gain the v4 columns in place (``_V4_COLUMNS``).
"""

from __future__ import annotations

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Sequence

import duckdb

from alicedev.store import migrate_v3

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 4

# Columns added in v4 (ARCHITECTURE §10). ``CREATE TABLE IF NOT EXISTS`` leaves an
# existing v3 table untouched, so these are added in place; all are nullable.
_V4_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("sessions", "assigned_by", "BIGINT"),
    ("messages", "sender_name", "TEXT"),
    ("messages", "content", "TEXT"),
)


class Store:
    """Async facade over one DuckDB connection."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alicedev-db")
        self._conn_lock = asyncio.Lock()
        self.lock = asyncio.Lock()

    async def open(self) -> None:
        """Open the connection, migrate a v2 database, apply schema v4 (idempotent)."""
        loop = asyncio.get_running_loop()
        self._conn = await loop.run_in_executor(
            self._executor, functools.partial(duckdb.connect, self._db_path)
        )
        schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        await self._run(lambda c: migrate_v3.migrate_if_needed(c, schema_sql))
        await self._run(lambda c: c.execute(schema_sql))
        for table, column, sql_type in _V4_COLUMNS:
            await self.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {sql_type}")
        await self._reconcile_sequences()
        await self.execute(
            "INSERT INTO schema_version (version, applied_at) "
            "SELECT ?, (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') "
            "WHERE NOT EXISTS (SELECT 1 FROM schema_version WHERE version >= ?)",
            (SCHEMA_VERSION, SCHEMA_VERSION),
        )
        # DuckDB 1.5.5 cannot replay some ALTER records before the default
        # database is initialized. Flush startup DDL/DML before serving so a
        # normal container stop never leaves those records in the WAL.
        await self.execute("CHECKPOINT")

    async def _reconcile_sequences(self) -> None:
        """Reseed surrogate-key sequences past the current max id.

        A DuckDB sequence can reset to its start value when the database is
        reopened while rows persist, so recreate each sequence past ``max(id)``.
        """
        for sequence, table, column in (
            ("seq_sessions", "sessions", "session_id"),
            ("seq_favorites", "favorites", "id"),
            ("seq_outbox", "outbox", "seq"),
        ):
            row = await self.fetch_one(f"SELECT COALESCE(MAX({column}), 0) FROM {table}")
            start = (int(row[0]) if row else 0) + 1
            await self.execute(f"DROP SEQUENCE IF EXISTS {sequence}")
            await self.execute(f"CREATE SEQUENCE {sequence} START {start}")

    async def close(self) -> None:
        if self._conn is not None:
            try:
                await self.execute("CHECKPOINT")
            except Exception:  # noqa: BLE001 - closing must not fail on checkpoint
                pass
            await self._run(lambda c: c.close())
            self._conn = None
        self._executor.shutdown(wait=True)

    async def _run(self, fn: Callable[[duckdb.DuckDBPyConnection], Any]) -> Any:
        if self._conn is None:
            raise RuntimeError("Store is not open")
        conn = self._conn
        loop = asyncio.get_running_loop()
        async with self._conn_lock:
            return await loop.run_in_executor(self._executor, functools.partial(fn, conn))

    async def transaction(self, fn: Callable[[duckdb.DuckDBPyConnection], Any]) -> Any:
        """Run ``fn(conn)`` inside BEGIN/COMMIT (ROLLBACK on error)."""

        def _tx(conn: duckdb.DuckDBPyConnection) -> Any:
            conn.execute("BEGIN")
            try:
                result = fn(conn)
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            return result

        return await self._run(_tx)

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        await self._run(lambda c: c.execute(sql, list(params)))

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        return await self._run(lambda c: c.execute(sql, list(params)).fetchall())

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> tuple | None:
        return await self._run(lambda c: c.execute(sql, list(params)).fetchone())

    async def next_id(self, sequence: str) -> int:
        row = await self.fetch_one(f"SELECT nextval('{sequence}')")
        assert row is not None
        return int(row[0])
