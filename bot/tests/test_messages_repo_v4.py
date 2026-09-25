"""Messages repository behavior for schema v4 metadata."""

from __future__ import annotations

import asyncio
from pathlib import Path

import rt_support  # noqa: F401 - puts bot/ on sys.path

from alicedev.store.db import Store
from alicedev.store.messages_repo import MessagesRepo


def test_claim_stores_v4_fields_and_dedupes_after_attach(tmp_path: Path) -> None:
    async def main() -> None:
        store = Store(str(tmp_path / "messages.duckdb"))
        try:
            await store.open()
            messages = MessagesRepo(store)

            claimed = await messages.claim(
                msg_ref="m-original",
                chat_key="chat-a",
                platform_message_id="platform-17",
                sender_key="user-1",
                sender_name="Ada",
                content="original platform content",
                text="initial injected prompt",
                session_id=41,
                agent_ref=None,
            )
            assert claimed.fresh
            assert await store.fetch_one(
                "SELECT sender_name, content, text FROM messages WHERE msg_ref = ?",
                ("m-original",),
            ) == ("Ada", "original platform content", "initial injected prompt")

            await messages.attach(
                "m-original", agent_ref="agent-current", text="updated injected prompt"
            )
            assert await store.fetch_one(
                "SELECT sender_name, content, text FROM messages WHERE msg_ref = ?",
                ("m-original",),
            ) == ("Ada", "original platform content", "updated injected prompt")

            duplicate = await messages.claim(
                msg_ref="m-retry",
                chat_key="chat-a",
                platform_message_id="platform-17",
                sender_key="user-retry",
                sender_name="Retry sender",
                content="replacement content",
                text="replacement prompt",
                session_id=99,
                agent_ref="agent-retry",
            )
            assert not duplicate.fresh
            assert duplicate.msg_ref == "m-original"
            assert duplicate.session_id == 41
            assert await store.fetch_one(
                "SELECT COUNT(*) FROM messages WHERE chat_key = ? AND platform_message_id = ?",
                ("chat-a", "platform-17"),
            ) == (1,)
        finally:
            await store.close()

    asyncio.run(main())
