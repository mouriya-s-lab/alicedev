"""messages table repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alicedev.store.db import Store


@dataclass
class MessageRecord:
    msg_ref: str
    session_ref: str
    chat_key: str
    platform_message_id: str | None
    sender_key: str | None
    text: str
    created_at: datetime | None = None


def _row_to_record(row: tuple) -> MessageRecord:
    return MessageRecord(
        msg_ref=row[0],
        session_ref=row[1],
        chat_key=row[2],
        platform_message_id=row[3],
        sender_key=row[4],
        text=row[5],
        created_at=row[6],
    )


class MessagesRepo:
    def __init__(self, store: "Store") -> None:
        self._store = store

    async def insert(
        self,
        *,
        msg_ref: str,
        session_ref: str,
        chat_key: str,
        platform_message_id: str | None,
        sender_key: str | None,
        text: str,
    ) -> None:
        await self._store.execute(
            "INSERT INTO messages (msg_ref, session_ref, chat_key, "
            "platform_message_id, sender_key, text) VALUES (?, ?, ?, ?, ?, ?)",
            (msg_ref, session_ref, chat_key, platform_message_id, sender_key, text),
        )

    async def claim(
        self,
        *,
        msg_ref: str,
        session_ref: str,
        chat_key: str,
        platform_message_id: str,
        sender_key: str | None,
        text: str,
    ) -> MessageRecord:
        """Atomically claim one inbound platform message.

        ``(chat_key, platform_message_id)`` is the durable idempotency key.
        The returned row is the winner, which lets callers reuse an existing
        session without sending another Paseo turn.
        """
        await self._store.execute(
            "INSERT INTO messages (msg_ref, session_ref, chat_key, "
            "platform_message_id, sender_key, text) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (chat_key, platform_message_id) DO NOTHING",
            (msg_ref, session_ref, chat_key, platform_message_id, sender_key, text),
        )
        record = await self.find_by_platform_message(chat_key, platform_message_id)
        if record is None:
            raise RuntimeError("inbound message claim disappeared")
        return record

    async def find_by_platform_message(
        self, chat_key: str, platform_message_id: str
    ) -> MessageRecord | None:
        row = await self._store.fetch_one(
            "SELECT msg_ref, session_ref, chat_key, platform_message_id, sender_key, "
            "text, created_at FROM messages "
            "WHERE chat_key = ? AND platform_message_id = ?",
            (chat_key, platform_message_id),
        )
        return _row_to_record(row) if row else None

    async def delete(self, msg_ref: str) -> None:
        await self._store.execute("DELETE FROM messages WHERE msg_ref = ?", (msg_ref,))

    async def get(self, msg_ref: str) -> MessageRecord | None:
        row = await self._store.fetch_one(
            "SELECT msg_ref, session_ref, chat_key, platform_message_id, sender_key, "
            "text, created_at FROM messages WHERE msg_ref = ?",
            (msg_ref,),
        )
        return _row_to_record(row) if row else None
