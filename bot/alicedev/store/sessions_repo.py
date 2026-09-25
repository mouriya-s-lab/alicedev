"""User-visible sessions (ARCHITECTURE §2, §10) and the per-chat current pointer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Mapping

from alicedev.domain import SessionView
from alicedev.render.pagination import DEFAULT_PAGE_SIZE

if TYPE_CHECKING:
    from alicedev.store.db import Store

NAME_LIMIT = 60


def bounded_session_name(text: str, limit: int = NAME_LIMIT) -> str:
    body = " ".join(str(text).split()).strip() or "未命名会话"
    return body if len(body) <= limit else body[: limit - 1] + "…"


@dataclass(frozen=True)
class SessionRow:
    session_id: int
    chat_key: str
    no: int
    scenario: str
    name: str
    created_by: str
    input: Mapping[str, Any]
    state: str
    workspace_id: str | None
    worktree_path: str | None
    base_sha: str | None
    data: Mapping[str, Any]
    created_at: datetime
    updated_at: datetime
    assigned_by: int | None


_COLUMNS = (
    "session_id, chat_key, no, scenario, name, created_by, input, state, workspace_id, "
    "worktree_path, base_sha, data, created_at, updated_at, assigned_by"
)


def _json(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        loaded = json.loads(value) if value.strip() else {}
        return loaded if isinstance(loaded, dict) else {}
    return dict(value) if isinstance(value, Mapping) else {}


def _row(row: tuple) -> SessionRow:
    return SessionRow(
        session_id=int(row[0]),
        chat_key=str(row[1]),
        no=int(row[2]),
        scenario=str(row[3]),
        name=str(row[4]),
        created_by=str(row[5]),
        input=_json(row[6]),
        state=str(row[7]),
        workspace_id=row[8],
        worktree_path=row[9],
        base_sha=row[10],
        data=_json(row[11]),
        created_at=row[12],
        updated_at=row[13],
        assigned_by=int(row[14]) if row[14] is not None else None,
    )


class SessionsRepo:
    def __init__(self, store: "Store") -> None:
        self._store = store

    async def create(
        self,
        *,
        chat_key: str,
        scenario: str,
        name: str,
        created_by: str,
        input: Mapping[str, Any],
        state: str,
        assigned_by: int | None,
    ) -> SessionRow:
        """Allocate the next per-chat number and insert (caller holds store.lock)."""
        session_id = await self._store.next_id("seq_sessions")
        row = await self._store.fetch_one(
            "SELECT COALESCE(MAX(no), 0) + 1 FROM sessions WHERE chat_key = ?", (chat_key,)
        )
        no = int(row[0]) if row else 1
        await self._store.execute(
            "INSERT INTO sessions (session_id, chat_key, no, scenario, name, created_by, "
            "input, state, data, assigned_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                session_id, chat_key, no, scenario, bounded_session_name(name), created_by,
                json.dumps(dict(input), ensure_ascii=False, default=str), state, "{}", assigned_by,
            ),
        )
        created = await self.get(session_id)
        assert created is not None
        return created

    async def get(self, session_id: int) -> SessionRow | None:
        row = await self._store.fetch_one(
            f"SELECT {_COLUMNS} FROM sessions WHERE session_id = ?", (session_id,)
        )
        return _row(row) if row else None

    async def by_no(self, chat_key: str, no: int) -> SessionRow | None:
        row = await self._store.fetch_one(
            f"SELECT {_COLUMNS} FROM sessions WHERE chat_key = ? AND no = ?", (chat_key, no)
        )
        return _row(row) if row else None

    async def set_state(self, session_id: int, state: str) -> None:
        await self._store.execute(
            "UPDATE sessions SET state = ?, updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') "
            "WHERE session_id = ?",
            (state, session_id),
        )

    async def merge_data(self, session_id: int, data: Mapping[str, Any]) -> None:
        current = await self.get(session_id)
        merged = dict(current.data if current else {})
        merged.update(data)
        await self._store.execute(
            "UPDATE sessions SET data = ? WHERE session_id = ?",
            (json.dumps(merged, ensure_ascii=False), session_id),
        )

    async def set_workspace(
        self,
        session_id: int,
        *,
        workspace_id: str | None,
        worktree_path: str | None = None,
        base_sha: str | None = None,
    ) -> None:
        await self._store.execute(
            "UPDATE sessions SET workspace_id = ?, worktree_path = COALESCE(?, worktree_path), "
            "base_sha = COALESCE(?, base_sha) WHERE session_id = ?",
            (workspace_id, worktree_path, base_sha, session_id),
        )

    async def rename(self, session_id: int, name: str) -> None:
        await self._store.execute(
            "UPDATE sessions SET name = ?, updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') "
            "WHERE session_id = ?",
            (bounded_session_name(name), session_id),
        )

    # --- current pointer ------------------------------------------------

    async def current(self, chat_key: str) -> SessionRow | None:
        row = await self._store.fetch_one(
            f"SELECT {', '.join('s.' + c.strip() for c in _COLUMNS.split(','))} "
            "FROM chat_current_sessions AS c JOIN sessions AS s ON s.session_id = c.session_id "
            "WHERE c.chat_key = ?",
            (chat_key,),
        )
        return _row(row) if row else None

    async def set_current(self, chat_key: str, session_id: int) -> None:
        await self._store.execute(
            "INSERT INTO chat_current_sessions (chat_key, session_id, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP AT TIME ZONE 'UTC') "
            "ON CONFLICT (chat_key) DO UPDATE SET session_id = excluded.session_id, "
            "updated_at = excluded.updated_at",
            (chat_key, session_id),
        )

    async def clear_current_if(self, chat_key: str, session_id: int) -> None:
        await self._store.execute(
            "DELETE FROM chat_current_sessions WHERE chat_key = ? AND session_id = ?",
            (chat_key, session_id),
        )

    # --- queries -------------------------------------------------------------

    async def in_states(self, states: tuple[str, ...]) -> list[SessionRow]:
        if not states:
            return []
        marks = ",".join("?" for _ in states)
        rows = await self._store.fetch_all(
            f"SELECT {_COLUMNS} FROM sessions WHERE state IN ({marks}) ORDER BY session_id",
            states,
        )
        return [_row(r) for r in rows]

    async def not_in_states(self, states: tuple[str, ...]) -> list[SessionRow]:
        marks = ",".join("?" for _ in states) or "''"
        rows = await self._store.fetch_all(
            f"SELECT {_COLUMNS} FROM sessions WHERE state NOT IN ({marks}) ORDER BY session_id",
            states,
        )
        return [_row(r) for r in rows]

    async def page(
        self,
        chat_key: str,
        *,
        page: int,
        scenario: str | None,
        include_ended: bool,
        ended_states: tuple[str, ...],
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[list[SessionRow], int]:
        where = ["chat_key = ?"]
        params: list[Any] = [chat_key]
        if scenario:
            where.append("scenario = ?")
            params.append(scenario)
        if not include_ended and ended_states:
            where.append(f"state NOT IN ({','.join('?' for _ in ended_states)})")
            params.extend(ended_states)
        clause = " AND ".join(where)
        total_row = await self._store.fetch_one(f"SELECT COUNT(*) FROM sessions WHERE {clause}", params)
        total = int(total_row[0]) if total_row else 0
        rows = await self._store.fetch_all(
            f"SELECT {_COLUMNS} FROM sessions WHERE {clause} ORDER BY no DESC LIMIT ? OFFSET ?",
            [*params, page_size, max(0, page - 1) * page_size],
        )
        return [_row(r) for r in rows], total

    async def last_activity(self, session_id: int) -> datetime | None:
        row = await self._store.fetch_one(
            "SELECT MAX(last_activity_at) FROM agents WHERE session_id = ?", (session_id,)
        )
        return row[0] if row else None

    async def counts(self, ended_states: tuple[str, ...]) -> dict[str, int]:
        marks = ",".join("?" for _ in ended_states) or "''"
        active = await self._store.fetch_one(
            f"SELECT COUNT(*) FROM sessions WHERE state NOT IN ({marks}) AND state <> 'queued'",
            ended_states,
        )
        queued = await self._store.fetch_one("SELECT COUNT(*) FROM sessions WHERE state = 'queued'")
        return {"active": int(active[0]) if active else 0, "queued": int(queued[0]) if queued else 0}

    async def view(self, row: SessionRow) -> SessionView:
        current = await self._store.fetch_one(
            "SELECT 1 FROM chat_current_sessions WHERE chat_key = ? AND session_id = ?",
            (row.chat_key, row.session_id),
        )
        return SessionView(
            session_id=row.session_id,
            chat_key=row.chat_key,
            no=row.no,
            name=row.name,
            scenario=row.scenario,
            state=row.state,
            created_by=row.created_by,
            created_at=row.created_at,
            last_activity_at=await self.last_activity(row.session_id),
            is_current=current is not None,
        )

    async def known_chats(self, finished_states: tuple[str, ...]) -> list[KnownChat]:
        finished_marks = ",".join("?" for _ in finished_states)
        open_count = (
            f"COUNT(*) FILTER (WHERE state NOT IN ({finished_marks}))"
            if finished_states
            else "COUNT(*)"
        )
        rows = await self._store.fetch_all(
            f"""WITH session_counts AS (
                    SELECT chat_key, {open_count} AS open_sessions
                    FROM sessions
                    GROUP BY chat_key
                ),
                latest_sessions AS (
                    SELECT chat_key, input,
                           ROW_NUMBER() OVER (
                               PARTITION BY chat_key
                               ORDER BY created_at DESC, session_id DESC
                           ) AS session_rank
                    FROM sessions
                ),
                chat_activity AS (
                    SELECT s.chat_key, MAX(a.last_activity_at) AS last_activity_at
                    FROM sessions AS s
                    LEFT JOIN agents AS a ON a.session_id = s.session_id
                    GROUP BY s.chat_key
                )
                SELECT latest.chat_key,
                       COALESCE(json_extract_string(latest.input, '$.chat.name'), '') AS name,
                       session_counts.open_sessions,
                       chat_activity.last_activity_at
                FROM latest_sessions AS latest
                JOIN session_counts USING (chat_key)
                JOIN chat_activity USING (chat_key)
                WHERE latest.session_rank = 1
                ORDER BY chat_activity.last_activity_at DESC NULLS LAST, latest.chat_key ASC""",
            finished_states,
        )
        return [
            KnownChat(
                chat_key=row[0],
                name=row[1],
                open_sessions=int(row[2]),
                last_activity_at=row[3],
            )
            for row in rows
        ]


@dataclass(frozen=True)
class KnownChat:
    chat_key: str
    name: str
    open_sessions: int
    last_activity_at: datetime | None
