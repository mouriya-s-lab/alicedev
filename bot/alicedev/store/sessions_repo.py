"""sessions table repository and its domain types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alicedev.store.db import Store


MAX_SESSION_NAME_LENGTH = 60


class SessionStatus(str, Enum):
    CREATING = "creating"
    ACTIVE = "active"
    CLOSED = "closed"
    FAILED = "failed"
    ARCHIVED = "archived"


def normalize_session_name_segment(value: str | None) -> str:
    """Return the first non-empty line with stable whitespace normalization."""
    if value is not None:
        for line in str(value).splitlines():
            normalized = " ".join(line.split())
            if normalized:
                return normalized
    return "未命名"


def bounded_session_name(prefix: str, value: str | None) -> str:
    """Build a display name bounded to the contract's codepoint limit."""
    segment = normalize_session_name_segment(value)
    available = MAX_SESSION_NAME_LENGTH - len(prefix)
    if available <= 0:
        return prefix[:MAX_SESSION_NAME_LENGTH]
    return f"{prefix}{segment[:available].rstrip()}"

def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class SessionRecord:
    session_ref: str
    chat_key: str
    template: str
    name: str
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
    "session_ref, chat_key, template, name, provider, model, thinking, agent_id, "
    "workspace_id, server_id, status, created_by, created_at, last_activity_at"
)
_QUALIFIED_COLUMNS = ", ".join(f"s.{column.strip()}" for column in _COLUMNS.split(","))


def _row_to_record(row: tuple) -> SessionRecord:
    return SessionRecord(
        session_ref=row[0],
        chat_key=row[1],
        template=row[2],
        name=row[3],
        provider=row[4],
        model=row[5],
        thinking=row[6],
        agent_id=row[7],
        workspace_id=row[8],
        server_id=row[9],
        status=SessionStatus(row[10]),
        created_by=row[11],
        created_at=row[12],
        last_activity_at=row[13],
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
        name: str,
        created_by: str,
        provider: str | None,
        model: str | None,
        thinking: str | None,
    ) -> None:
        created_at = _utc_now_naive()
        await self._store.execute(
            "INSERT INTO sessions (session_ref, chat_key, template, name, provider, "
            "model, thinking, status, created_by, created_at, last_activity_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_ref,
                chat_key,
                template,
                name,
                provider,
                model,
                thinking,
                SessionStatus.CREATING.value,
                created_by,
                created_at,
                created_at,
            ),
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
        ts = _utc_now_naive() if when is None else _utc_naive(when)
        await self._store.execute(
            "UPDATE sessions SET last_activity_at = GREATEST(last_activity_at, ?) "
            "WHERE session_ref = ?",
            (ts, session_ref),
        )


    async def current_for_chat(self, chat_key: str) -> SessionRecord | None:
        """Return the explicitly selected session for ``chat_key`` only."""
        row = await self._store.fetch_one(
            f"SELECT {_QUALIFIED_COLUMNS} "
            "FROM chat_current_sessions AS c "
            "JOIN sessions AS s "
            "ON s.session_ref = c.current_session_ref AND s.chat_key = c.chat_key "
            "WHERE c.chat_key = ? AND s.status IN (?, ?)",
            (chat_key, SessionStatus.ACTIVE.value, SessionStatus.CLOSED.value),
        )
        return _row_to_record(row) if row else None

    async def set_current(self, chat_key: str, session_ref: str) -> None:
        """Select an active or closed session belonging to ``chat_key``."""
        await self._store.execute(
            "INSERT INTO chat_current_sessions "
            "(chat_key, current_session_ref, updated_at) "
            "SELECT ?, session_ref, ? FROM sessions "
            "WHERE session_ref = ? AND chat_key = ? AND status IN (?, ?) "
            "ON CONFLICT (chat_key) DO UPDATE SET "
            "current_session_ref = excluded.current_session_ref, "
            "updated_at = excluded.updated_at",
            (
                chat_key,
                _utc_now_naive(),
                session_ref,
                chat_key,
                SessionStatus.ACTIVE.value,
                SessionStatus.CLOSED.value,
            ),
        )

    async def clear_current(self, chat_key: str, expected_ref: str | None = None) -> None:
        if expected_ref is None:
            await self._store.execute(
                "DELETE FROM chat_current_sessions WHERE chat_key = ?", (chat_key,)
            )
            return
        await self._store.execute(
            "DELETE FROM chat_current_sessions "
            "WHERE chat_key = ? AND current_session_ref = ?",
            (chat_key, expected_ref),
        )


    async def idle_active(self, older_than: datetime) -> list[SessionRecord]:
        cutoff = _utc_naive(older_than)
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
