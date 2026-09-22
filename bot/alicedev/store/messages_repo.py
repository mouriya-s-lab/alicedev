"""Inbound messages injected into agents; also the redelivery dedupe key."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alicedev.store.db import Store


@dataclass(frozen=True)
class ClaimedMessage:
    msg_ref: str
    session_id: int | None
    agent_ref: str | None
    fresh: bool


class MessagesRepo:
    def __init__(self, store: "Store") -> None:
        self._store = store

    async def claim(
        self,
        *,
        msg_ref: str,
        chat_key: str,
        platform_message_id: str | None,
        sender_key: str | None,
        text: str,
        session_id: int | None,
        agent_ref: str | None,
    ) -> ClaimedMessage:
        """Insert unless ``(chat_key, platform_message_id)`` was already seen."""
        if platform_message_id is not None:
            existing = await self._store.fetch_one(
                "SELECT msg_ref, session_id, agent_ref FROM messages "
                "WHERE chat_key = ? AND platform_message_id = ?",
                (chat_key, platform_message_id),
            )
            if existing is not None:
                return ClaimedMessage(str(existing[0]), existing[1], existing[2], fresh=False)
        await self._store.execute(
            "INSERT INTO messages (msg_ref, agent_ref, chat_key, platform_message_id, sender_key, "
            "text, session_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (msg_ref, agent_ref, chat_key, platform_message_id, sender_key, text, session_id),
        )
        return ClaimedMessage(msg_ref, session_id, agent_ref, fresh=True)

    async def attach(self, msg_ref: str, *, agent_ref: str, text: str | None = None) -> None:
        if text is None:
            await self._store.execute(
                "UPDATE messages SET agent_ref = ? WHERE msg_ref = ?", (agent_ref, msg_ref)
            )
        else:
            await self._store.execute(
                "UPDATE messages SET agent_ref = ?, text = ? WHERE msg_ref = ?",
                (agent_ref, text, msg_ref),
            )

    async def delete(self, msg_ref: str) -> None:
        await self._store.execute("DELETE FROM messages WHERE msg_ref = ?", (msg_ref,))
