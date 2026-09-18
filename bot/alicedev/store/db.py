"""Single-writer DuckDB store.

DuckDB's Python connection is not safe for concurrent use, so every query runs
on a dedicated single-thread executor serialized by an internal connection lock.
``Store.lock`` is a *separate* asyncio.Lock repositories take for check-then-write
sequences that must be atomic across several queries.
"""

from __future__ import annotations

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Sequence

import duckdb

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_SCHEMA_VERSION = 1


class Store:
    """Async facade over one DuckDB connection."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alicedev-db")
        self._conn_lock = asyncio.Lock()
        self.lock = asyncio.Lock()

    async def open(self) -> None:
        """Open the connection and apply the schema (idempotent)."""
        loop = asyncio.get_running_loop()
        self._conn = await loop.run_in_executor(
            self._executor, functools.partial(duckdb.connect, self._db_path)
        )
        schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        await self._run(lambda c: c.execute(schema_sql))
        await self._reconcile_sequences()
        rows = await self.fetch_all("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
        if not rows:
            await self.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (_SCHEMA_VERSION,)
            )

    async def _reconcile_sequences(self) -> None:
        """Reseed surrogate-key sequences past the current max id.

        A DuckDB sequence can reset to its start value when the database is
        reopened while table rows persist, so ``nextval`` would return an id that
        already exists and violate the primary key. Recreate each sequence to
        start just past ``max(id)`` so allocation is always collision-free.
        """
        for sequence, table in (
            ("seq_requirements", "requirements"),
            ("seq_favorites", "favorites"),
        ):
            row = await self.fetch_one(f"SELECT COALESCE(MAX(id), 0) FROM {table}")
            start = (int(row[0]) if row else 0) + 1
            await self.execute(f"DROP SEQUENCE IF EXISTS {sequence}")
            await self.execute(f"CREATE SEQUENCE {sequence} START {start}")

    async def close(self) -> None:
        if self._conn is not None:
            await self._run(lambda c: c.close())
            self._conn = None
        self._executor.shutdown(wait=True)

    async def _run(self, fn) -> Any:
        if self._conn is None:
            raise RuntimeError("Store is not open")
        conn = self._conn
        loop = asyncio.get_running_loop()
        async with self._conn_lock:
            return await loop.run_in_executor(self._executor, functools.partial(fn, conn))

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        await self._run(lambda c: c.execute(sql, list(params)))

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        def _q(c: duckdb.DuckDBPyConnection) -> list[tuple]:
            return c.execute(sql, list(params)).fetchall()

        return await self._run(_q)

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> tuple | None:
        def _q(c: duckdb.DuckDBPyConnection) -> tuple | None:
            return c.execute(sql, list(params)).fetchone()

        return await self._run(_q)

    async def next_id(self, sequence: str) -> int:
        row = await self.fetch_one(f"SELECT nextval('{sequence}')")
        assert row is not None
        return int(row[0])
