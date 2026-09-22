"""Outbound queue rows (ARCHITECTURE §6).

``queued`` rows are due now or at ``next_attempt_at``; a delivery failure keeps
the row ``queued`` with a backoff until retries run out, then it becomes
``failed`` (dead, ``next_attempt_at`` NULL). Per chat, rows are delivered in
``seq`` order and a pending head row blocks later rows of the same chat.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping, Sequence

if TYPE_CHECKING:
    from alicedev.store.db import Store

MAX_ATTEMPTS = 8


class OutboxState(str, Enum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"


@dataclass(frozen=True)
class OutboxRow:
    reply_id: str
    seq: int
    chat_key: str
    session_id: int | None
    agent_ref: str | None
    msgs: tuple[str, ...]
    payload: Mapping[str, Any]
    payload_sha256: str
    state: OutboxState
    attempts: int
    platform_message_ids: tuple[str, ...]


_COLUMNS = (
    "reply_id, seq, chat_key, session_id, agent_ref, msgs, payload, payload_sha256, state, "
    "attempts, platform_message_ids"
)


def _load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value) if value.strip() else default
    return value


def _row(row: tuple) -> OutboxRow:
    return OutboxRow(
        reply_id=str(row[0]),
        seq=int(row[1]),
        chat_key=str(row[2]),
        session_id=int(row[3]) if row[3] is not None else None,
        agent_ref=row[4],
        msgs=tuple(str(m) for m in _load(row[5], [])),
        payload=_load(row[6], {}),
        payload_sha256=str(row[7]),
        state=OutboxState(str(row[8])),
        attempts=int(row[9]),
        platform_message_ids=tuple(str(m) for m in _load(row[10], [])),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(300, 2 ** max(0, attempts)))


class OutboxRepo:
    def __init__(self, store: "Store") -> None:
        self._store = store

    async def get(self, reply_id: str) -> OutboxRow | None:
        row = await self._store.fetch_one(
            f"SELECT {_COLUMNS} FROM outbox WHERE reply_id = ?", (reply_id,)
        )
        return _row(row) if row else None

    async def insert(
        self,
        *,
        reply_id: str,
        chat_key: str,
        session_id: int | None,
        agent_ref: str | None,
        msgs: Sequence[str],
        payload: Mapping[str, Any],
        payload_sha256: str,
    ) -> None:
        """Insert a queued row (caller holds store.lock for idempotency checks)."""
        seq = await self._store.next_id("seq_outbox")
        await self._store.execute(
            "INSERT INTO outbox (reply_id, seq, chat_key, session_id, agent_ref, msgs, payload, "
            "payload_sha256, state, attempts, next_attempt_at) "
            "VALUES (?,?,?,?,?,?,?,?, 'queued', 0, CURRENT_TIMESTAMP AT TIME ZONE 'UTC')",
            (
                reply_id, seq, chat_key, session_id, agent_ref, json.dumps(list(msgs)),
                json.dumps(dict(payload), ensure_ascii=False, default=str), payload_sha256,
            ),
        )

    async def due_heads(self, now: datetime | None = None) -> list[OutboxRow]:
        """Per chat, the oldest queued row if it is due."""
        moment = now or _now()
        rows = await self._store.fetch_all(
            f"SELECT {_COLUMNS}, next_attempt_at FROM outbox AS o WHERE state = 'queued' "
            "AND seq = (SELECT MIN(seq) FROM outbox WHERE state = 'queued' "
            "AND chat_key = o.chat_key) ORDER BY seq",
        )
        return [_row(r[:11]) for r in rows if r[11] is None or r[11] <= moment]

    async def mark_sent(self, reply_id: str, platform_message_ids: Sequence[str]) -> None:
        await self._store.execute(
            "UPDATE outbox SET state = 'sent', platform_message_ids = ?, attempts = attempts + 1, "
            "next_attempt_at = NULL, last_error = NULL, "
            "updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') WHERE reply_id = ?",
            (json.dumps(list(platform_message_ids)), reply_id),
        )

    async def mark_attempt_failed(self, row: OutboxRow, error: str) -> OutboxState:
        attempts = row.attempts + 1
        if attempts >= MAX_ATTEMPTS:
            await self._store.execute(
                "UPDATE outbox SET state = 'failed', attempts = ?, next_attempt_at = NULL, "
                "last_error = ?, updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') "
                "WHERE reply_id = ?",
                (attempts, error[:2000], row.reply_id),
            )
            return OutboxState.FAILED
        await self._store.execute(
            "UPDATE outbox SET attempts = ?, next_attempt_at = ?, last_error = ?, "
            "updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') WHERE reply_id = ?",
            (attempts, _now() + backoff(attempts), error[:2000], row.reply_id),
        )
        return OutboxState.QUEUED

    async def pending_count(self) -> int:
        row = await self._store.fetch_one("SELECT COUNT(*) FROM outbox WHERE state = 'queued'")
        return int(row[0]) if row else 0
