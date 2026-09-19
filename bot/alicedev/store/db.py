"""Single-writer DuckDB store.

DuckDB's Python connection is not safe for concurrent use, so every query runs
on a dedicated single-thread executor serialized by an internal connection lock.
``Store.lock`` is a *separate* asyncio.Lock repositories take for check-then-write
sequences that must be atomic across several queries.
"""

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import duckdb

from alicedev.store.sessions_repo import SessionStatus, bounded_session_name

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_SCHEMA_VERSION = 2
_LEGACY_TIMESTAMP_MIGRATION = "v2_legacy_timestamps_utc"
_LEGACY_TEMPLATE_LABELS = {
    "requirement": "需求",
    "investigate": "调查",
    "github-issue": "GitHub Issue",
    "github-pr": "GitHub PR",
}


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
        await self._migrate_to_v2()
        await self._reconcile_sequences()

    async def _migrate_to_v2(self) -> None:
        """Converge fresh and v1 databases to the v2 shape and UTC time base."""
        versions = await self.fetch_all("SELECT version FROM schema_version")
        prior_version = max((int(row[0]) for row in versions), default=0)

        await self.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "migration TEXT PRIMARY KEY, "
            "applied_at TIMESTAMP NOT NULL "
            "DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'))"
        )
        marker = await self.fetch_one(
            "SELECT 1 FROM schema_migrations WHERE migration = ?",
            (_LEGACY_TIMESTAMP_MIGRATION,),
        )

        columns = await self.fetch_all("PRAGMA table_info('sessions')")
        if not any(str(row[1]) == "name" for row in columns):
            await self.execute("ALTER TABLE sessions ADD COLUMN name TEXT")
        await self.execute(
            "CREATE TABLE IF NOT EXISTS chat_current_sessions ("
            "chat_key TEXT PRIMARY KEY, "
            "current_session_ref TEXT NOT NULL, "
            "updated_at TIMESTAMP NOT NULL "
            "DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'))"
        )

        # Capture legacy local wall-clock labels before timestamp conversion.
        # Names are persisted user-facing data and must not shift with the
        # lifecycle timestamp migration.
        await self._repair_current_sessions()
        await self._backfill_session_names()
        if marker is None:
            if prior_version < _SCHEMA_VERSION:
                await self._convert_legacy_timestamps()
            else:
                await self.execute(
                    "INSERT INTO schema_migrations (migration, applied_at) "
                    "VALUES (?, CURRENT_TIMESTAMP AT TIME ZONE 'UTC')",
                    (_LEGACY_TIMESTAMP_MIGRATION,),
                )

        for table, column in (
            ("schema_version", "applied_at"),
            ("schema_migrations", "applied_at"),
            ("sessions", "created_at"),
            ("sessions", "last_activity_at"),
            ("chat_current_sessions", "updated_at"),
            ("messages", "created_at"),
            ("reply_deliveries", "created_at"),
            ("reply_deliveries", "updated_at"),
            ("outbound", "created_at"),
            ("requirements", "created_at"),
            ("favorites", "created_at"),
            ("reports", "created_at"),
            ("tokens_issued", "issued_at"),
        ):
            await self.execute(
                f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT "
                "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"
            )

        columns = await self.fetch_all("PRAGMA table_info('sessions')")
        name_column = next((row for row in columns if str(row[1]) == "name"), None)
        if name_column is not None and not bool(name_column[3]):
            await self.execute("ALTER TABLE sessions ALTER COLUMN name SET NOT NULL")

        await self.execute(
            "INSERT INTO schema_version (version, applied_at) "
            "SELECT ?, (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') "
            "WHERE NOT EXISTS "
            "(SELECT 1 FROM schema_version WHERE version >= ?)",
            (_SCHEMA_VERSION, _SCHEMA_VERSION),
        )

    async def _convert_legacy_timestamps(self) -> None:
        """Convert v1 local-naive timestamps once, atomically with its marker."""
        def _convert(connection) -> None:
            connection.execute("BEGIN")
            try:
                marker = connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE migration = ?",
                    (_LEGACY_TIMESTAMP_MIGRATION,),
                ).fetchone()
                if marker is not None:
                    connection.execute("COMMIT")
                    return

                cutover_row = connection.execute(
                    "SELECT CURRENT_TIMESTAMP AT TIME ZONE 'UTC'"
                ).fetchone()
                if not cutover_row:
                    raise RuntimeError("DuckDB did not return a migration cutoff")
                cutover = cutover_row[0]
                if isinstance(cutover, datetime) and cutover.tzinfo is not None:
                    cutover = cutover.astimezone(timezone.utc).replace(tzinfo=None)

                # Seed only missing legacy pointers before activity timestamps
                # are reset. Existing pointers are intentionally untouched.
                connection.execute(
                    "WITH ranked AS ("
                    "SELECT chat_key, session_ref, "
                    "ROW_NUMBER() OVER (PARTITION BY chat_key ORDER BY "
                    "CASE status WHEN 'active' THEN 0 WHEN 'closed' THEN 1 ELSE 2 END, "
                    "last_activity_at DESC NULLS LAST, session_ref ASC) AS rn "
                    "FROM sessions WHERE status IN ('active', 'closed')) "
                    "INSERT INTO chat_current_sessions "
                    "(chat_key, current_session_ref, updated_at) "
                    "SELECT chat_key, session_ref, ? FROM ranked WHERE rn = 1 "
                    "ON CONFLICT DO NOTHING",
                    (cutover,),
                )

                for table, column in (
                    ("schema_version", "applied_at"),
                    ("sessions", "created_at"),
                    ("messages", "created_at"),
                    ("reply_deliveries", "created_at"),
                    ("outbound", "created_at"),
                    ("requirements", "created_at"),
                    ("favorites", "created_at"),
                    ("reports", "created_at"),
                ):
                    condition = " WHERE version < 2" if table == "schema_version" else ""
                    connection.execute(
                        f"UPDATE {table} SET {column} = "
                        f"({column} AT TIME ZONE 'Asia/Shanghai') "
                        f"AT TIME ZONE 'UTC'{condition}"
                    )

                # One cutover applies to both live and closed sessions.
                connection.execute(
                    "UPDATE sessions SET last_activity_at = ? "
                    "WHERE status IN ('active', 'closed')",
                    (cutover,),
                )
                connection.execute(
                    "INSERT INTO schema_migrations (migration, applied_at) "
                    "VALUES (?, ?)",
                    (_LEGACY_TIMESTAMP_MIGRATION, cutover),
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

        await self._run(_convert)


    async def _repair_current_sessions(self) -> None:
        """Drop pointers that no longer identify a selectable session."""
        await self.execute(
            "DELETE FROM chat_current_sessions "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM sessions AS s "
            "WHERE s.session_ref = chat_current_sessions.current_session_ref "
            "AND s.chat_key = chat_current_sessions.chat_key "
            "AND s.status IN ('active', 'closed'))"
        )

    async def _backfill_session_names(self) -> None:
        requirements: dict[str, str] = {}
        for session_ref, text in await self.fetch_all(
            "SELECT session_ref, text FROM requirements "
            "WHERE session_ref IS NOT NULL ORDER BY id ASC"
        ):
            key = str(session_ref)
            if key not in requirements:
                requirements[key] = str(text)

        rows = await self.fetch_all(
            "SELECT session_ref, template, name, created_at FROM sessions"
        )
        for session_ref, template, current_name, created_at in rows:
            if current_name is not None and str(current_name).strip():
                continue
            ref = str(session_ref)
            template_name = str(template)
            requirement_text = requirements.get(ref)
            if template_name == "requirement" and requirement_text and requirement_text.strip():
                name = bounded_session_name("需求 · ", requirement_text)
            else:
                name = self._legacy_session_name(template_name, created_at)
            await self.execute(
                "UPDATE sessions SET name = ? "
                "WHERE session_ref = ? AND (name IS NULL OR trim(name) = '')",
                (name, ref),
            )


    @staticmethod
    def _legacy_session_name(template: str, created_at: object) -> str:
        label = _LEGACY_TEMPLATE_LABELS.get(template, template or "未知模板")
        stamp = Store._legacy_timestamp(created_at)
        return bounded_session_name(f"历史{label} · ", stamp)

    @staticmethod
    def _legacy_timestamp(value: object) -> str:
        if isinstance(value, datetime):
            timestamp = value
        else:
            try:
                timestamp = datetime.fromisoformat(str(value))
            except (TypeError, ValueError):
                timestamp = datetime(1970, 1, 1)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
        return timestamp.strftime("%Y-%m-%d %H:%M")

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
