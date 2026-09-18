"""sessions table repository and its domain types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alicedev.store.db import Store


class SessionStatus(str, Enum):
    CREATING = "creating"
    ACTIVE = "active"
    CLOSED = "closed"
    FAILED = "failed"
    ARCHIVED = "archived"


@dataclass
class SessionRecord:
    session_ref: str
    chat_key: str
    template: str
    status: SessionStatus
    created_by: str
    provider: str | None = None
    model: str | None = None
    thinking: str | None = None
    agent_id: str | None = None
    workspace_id: str | None = None
    server_id: str | None = None
    created_at: datetime | None = None
    last_activity_at: datetime | None = None


_COLUMNS = (
    "session_ref, chat_key, template, provider, model, thinking, agent_id, "
    "workspace_id, server_id, status, created_by, created_at, last_activity_at"
)


def _row_to_record(row: tuple) -> SessionRecord:
    return SessionRecord(
        session_ref=row[0],
        chat_key=row[1],
        template=row[2],
        provider=row[3],
        model=row[4],
        thinking=row[5],
        agent_id=row[6],
        workspace_id=row[7],
        server_id=row[8],
        status=SessionStatus(row[9]),
        created_by=row[10],
        created_at=row[11],
        last_activity_at=row[12],
    )


class SessionsRepo:
    def __init__(self, store: "Store") -> None:
        self._store = store

    async def create_creating(
        self,
        *,
        session_ref: str,
        chat_key: str,
        template: str,
        created_by: str,
        provider: str | None,
        model: str | None,
        thinking: str | None,
    ) -> None:
        await self._store.execute(
            "INSERT INTO sessions (session_ref, chat_key, template, provider, model, "
            "thinking, status, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_ref, chat_key, template, provider, model, thinking,
             SessionStatus.CREATING.value, created_by),
        )

    async def get(self, session_ref: str) -> SessionRecord | None:
        row = await self._store.fetch_one(
            f"SELECT {_COLUMNS} FROM sessions WHERE session_ref = ?", (session_ref,)
        )
        return _row_to_record(row) if row else None

    async def set_active(
        self,
        session_ref: str,
        *,
        agent_id: str,
        workspace_id: str | None,
        server_id: str | None,
        provider: str | None = None,
        model: str | None = None,
        thinking: str | None = None,
    ) -> None:
        await self._store.execute(
            "UPDATE sessions SET status = ?, agent_id = ?, workspace_id = ?, "
            "server_id = ?, provider = COALESCE(?, provider), "
            "model = COALESCE(?, model), thinking = COALESCE(?, thinking) "
            "WHERE session_ref = ?",
            (SessionStatus.ACTIVE.value, agent_id, workspace_id, server_id,
             provider, model, thinking, session_ref),
        )

    async def set_status(self, session_ref: str, status: SessionStatus) -> None:
        await self._store.execute(
            "UPDATE sessions SET status = ? WHERE session_ref = ?",
            (status.value, session_ref),
        )

    async def touch(self, session_ref: str, when: datetime | None = None) -> None:
        """Advance last_activity_at to max(current, when)."""
        ts = (when or datetime.now(timezone.utc)).replace(tzinfo=None)
        await self._store.execute(
            "UPDATE sessions SET last_activity_at = GREATEST(last_activity_at, ?) "
            "WHERE session_ref = ?",
            (ts, session_ref),
        )

    async def latest_active_for_chat(self, chat_key: str) -> SessionRecord | None:
        row = await self._store.fetch_one(
            f"SELECT {_COLUMNS} FROM sessions WHERE chat_key = ? AND status = ? "
            "ORDER BY last_activity_at DESC LIMIT 1",
            (chat_key, SessionStatus.ACTIVE.value),
        )
        return _row_to_record(row) if row else None

    async def idle_active(self, older_than: datetime) -> list[SessionRecord]:
        cutoff = older_than.replace(tzinfo=None)
        rows = await self._store.fetch_all(
            f"SELECT {_COLUMNS} FROM sessions WHERE status = ? AND last_activity_at < ?",
            (SessionStatus.ACTIVE.value, cutoff),
        )
        return [_row_to_record(r) for r in rows]

    async def counts(self) -> dict[str, int]:
        rows = await self._store.fetch_all(
            "SELECT status, COUNT(*) FROM sessions GROUP BY status"
        )
        return {status: int(n) for status, n in rows}
