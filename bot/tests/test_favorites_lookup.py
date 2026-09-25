"""Lookup favorites by their originating platform message."""

from __future__ import annotations

import asyncio
from pathlib import Path

import rt_support  # noqa: F401 - puts bot/ on sys.path

from alicedev.store.db import Store
from alicedev.store.favorites_repo import FavoritesRepo


async def _insert_favorite(
    repo: FavoritesRepo, *, chat_key: str, platform_message_id: str, text: str
) -> int:
    return await repo.insert(
        chat_key=chat_key,
        saver_key="telegram:saver",
        saver_name="Saver",
        author_key="telegram:author",
        author_name="Author",
        text=text,
        images=[],
        platform_message_id=platform_message_id,
    )


def test_by_platform_message_id_scopes_to_chat_and_returns_newest(tmp_path: Path) -> None:
    async def main() -> None:
        store = Store(str(tmp_path / "favorites.duckdb"))
        await store.open()
        try:
            repo = FavoritesRepo(store)
            older_id = await _insert_favorite(
                repo, chat_key="chat-a", platform_message_id="message-1", text="older"
            )
            newest_id = await _insert_favorite(
                repo, chat_key="chat-a", platform_message_id="message-1", text="newest"
            )
            other_chat_id = await _insert_favorite(
                repo, chat_key="chat-b", platform_message_id="message-1", text="other chat"
            )

            found = await repo.by_platform_message_id("chat-a", "message-1")
            assert found is not None
            assert (found.id, found.chat_key, found.text) == (newest_id, "chat-a", "newest")
            assert newest_id > older_id

            other_chat = await repo.by_platform_message_id("chat-b", "message-1")
            assert other_chat is not None
            assert (other_chat.id, other_chat.text) == (other_chat_id, "other chat")

            assert await repo.by_platform_message_id("chat-missing", "message-1") is None
            assert await repo.by_platform_message_id("chat-a", "message-missing") is None
        finally:
            await store.close()

    asyncio.run(main())
