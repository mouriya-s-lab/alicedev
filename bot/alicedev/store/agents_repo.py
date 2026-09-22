"""paseo agents that serve a session state (ARCHITECTURE §4, §10)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alicedev.store.db import Store


class AgentRowStatus(str, Enum):
    CREATING = "creating"
    ACTIVE = "active"
    CLOSED = "closed"
    FAILED = "failed"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class AgentRow:
    agent_ref: str
    session_id: int
    state: str
    provider: str
    agent_id: str | None
    workspace_id: str | None
    server_id: str | None
    status: AgentRowStatus
    legacy_ref: str | None
    created_at: datetime
    last_activity_at: datetime


_COLUMNS = (
    "agent_ref, session_id, state, provider, agent_id, workspace_id, server_id, status, "
    "legacy_ref, created_at, last_activity_at"
)


def _row(row: tuple) -> AgentRow:
    return AgentRow(
        agent_ref=str(row[0]),
        session_id=int(row[1]),
        state=str(row[2]),
        provider=str(row[3]),
        agent_id=row[4],
        workspace_id=row[5],
        server_id=row[6],
        status=AgentRowStatus(str(row[7])),
        legacy_ref=row[8],
        created_at=row[9],
        last_activity_at=row[10],
    )


class AgentsRepo:
    def __init__(self, store: "Store") -> None:
        self._store = store

    async def create_creating(
        self, *, agent_ref: str, session_id: int, state: str, provider: str
    ) -> None:
        await self._store.execute(
            "INSERT INTO agents (agent_ref, session_id, state, provider, status) "
            "VALUES (?, ?, ?, ?, 'creating')",
            (agent_ref, session_id, state, provider),
        )

    async def get(self, ref: str) -> AgentRow | None:
        """By ``a_`` ref, or by a migrated agent's legacy ``s_`` ref."""
        row = await self._store.fetch_one(
            f"SELECT {_COLUMNS} FROM agents WHERE agent_ref = ? OR legacy_ref = ?", (ref, ref)
        )
        return _row(row) if row else None

    async def set_active(
        self, agent_ref: str, *, agent_id: str, workspace_id: str | None, server_id: str | None
    ) -> None:
        await self._store.execute(
            "UPDATE agents SET agent_id = ?, workspace_id = ?, server_id = ?, status = 'active', "
            "last_activity_at = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') WHERE agent_ref = ?",
            (agent_id, workspace_id, server_id, agent_ref),
        )

    async def set_status(self, agent_ref: str, status: AgentRowStatus) -> None:
        await self._store.execute(
            "UPDATE agents SET status = ? WHERE agent_ref = ?", (status.value, agent_ref)
        )

    async def touch(self, agent_ref: str) -> None:
        await self._store.execute(
            "UPDATE agents SET last_activity_at = GREATEST(last_activity_at, "
            "CURRENT_TIMESTAMP AT TIME ZONE 'UTC') WHERE agent_ref = ?",
            (agent_ref,),
        )

    async def for_state(self, session_id: int, state: str) -> AgentRow | None:
        """The newest non-archived agent serving ``state`` of the session."""
        row = await self._store.fetch_one(
            f"SELECT {_COLUMNS} FROM agents WHERE session_id = ? AND state = ? "
            "AND status <> 'archived' ORDER BY created_at DESC LIMIT 1",
            (session_id, state),
        )
        return _row(row) if row else None

    async def latest(self, session_id: int) -> AgentRow | None:
        row = await self._store.fetch_one(
            f"SELECT {_COLUMNS} FROM agents WHERE session_id = ? AND agent_id IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        )
        return _row(row) if row else None

    async def unarchived(self, session_id: int) -> list[AgentRow]:
        rows = await self._store.fetch_all(
            f"SELECT {_COLUMNS} FROM agents WHERE session_id = ? AND status <> 'archived'",
            (session_id,),
        )
        return [_row(r) for r in rows]

    async def by_status(self, status: AgentRowStatus) -> list[AgentRow]:
        rows = await self._store.fetch_all(
            f"SELECT {_COLUMNS} FROM agents WHERE status = ?", (status.value,)
        )
        return [_row(r) for r in rows]

    async def idle_candidates(self, cutoff: datetime) -> list[AgentRow]:
        """Active agents whose session is still in the agent's (conversational) state."""
        rows = await self._store.fetch_all(
            f"SELECT {', '.join('a.' + c.strip() for c in _COLUMNS.split(','))} "
            "FROM agents AS a JOIN sessions AS s ON s.session_id = a.session_id "
            "WHERE a.status = 'active' AND s.state = a.state AND a.last_activity_at < ?",
            (cutoff,),
        )
        return [_row(r) for r in rows]

    async def counts(self) -> dict[str, int]:
        rows = await self._store.fetch_all("SELECT status, COUNT(*) FROM agents GROUP BY status")
        return {str(k): int(v) for k, v in rows}
