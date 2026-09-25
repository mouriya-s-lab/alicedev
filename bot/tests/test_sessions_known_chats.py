"""Tests for the known chat summary query."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import rt_support  # noqa: F401 - puts bot/ on sys.path

from alicedev.store.db import Store
from alicedev.store.sessions_repo import KnownChat, SessionsRepo


def test_known_chats_summarizes_sessions_and_agent_activity(tmp_path: Path) -> None:
    async def main() -> None:
        store = Store(str(tmp_path / "known-chats.duckdb"))
        await store.open()
        repo = SessionsRepo(store)

        async def insert_session(
            session_id: int,
            chat_key: str,
            no: int,
            state: str,
            input_data: dict[str, str | dict[str, str]],
            created_at: datetime,
        ) -> None:
            await store.execute(
                "INSERT INTO sessions "
                "(session_id, chat_key, no, scenario, name, created_by, input, state, "
                "workspace_id, worktree_path, base_sha, data, created_at, updated_at, assigned_by) "
                "VALUES (?, ?, ?, ?, ?, ?, CAST(? AS JSON), ?, NULL, NULL, NULL, "
                "CAST(? AS JSON), ?, ?, NULL)",
                (
                    session_id,
                    chat_key,
                    no,
                    "test",
                    f"session-{session_id}",
                    "user",
                    json.dumps(input_data),
                    state,
                    "{}",
                    created_at,
                    created_at,
                ),
            )

        async def insert_agent(agent_ref: str, session_id: int, last_activity_at: datetime) -> None:
            await store.execute(
                "INSERT INTO agents "
                "(agent_ref, session_id, state, provider, agent_id, workspace_id, server_id, "
                "status, legacy_ref, created_at, last_activity_at) "
                "VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, NULL, ?, ?)",
                (agent_ref, session_id, "discussing", "test", "active", last_activity_at, last_activity_at),
            )

        first_activity = datetime(2026, 9, 26, 9, 30)
        newer_activity = datetime(2026, 9, 26, 12)
        tied_created_at = datetime(2026, 9, 26, 8)
        try:
            await insert_session(1, "chat-b", 1, "archived", {"chat": {"name": "Old B"}}, tied_created_at)
            await insert_session(
                2, "chat-b", 2, "active", {"chat": {"name": "Newest B"}}, datetime(2026, 9, 26, 9)
            )
            await insert_session(
                3, "chat-a", 1, "active", {"chat": {"name": "Old A"}}, tied_created_at
            )
            await insert_session(4, "chat-a", 2, "done", {"other": "no chat name"}, datetime(2026, 9, 26, 10))
            await insert_session(
                5, "chat-c", 1, "done", {"chat": {"name": "No agents"}}, datetime(2026, 9, 26, 11)
            )

            await insert_agent("a_old_b", 1, first_activity)
            await insert_agent("a_new_b_1", 2, newer_activity)
            await insert_agent("a_new_b_2", 2, datetime(2026, 9, 26, 11, 45))
            await insert_agent("a_a", 3, newer_activity)

            chats = await repo.known_chats(("archived", "done"))
            assert chats == [
                KnownChat("chat-a", "", 1, newer_activity),
                KnownChat("chat-b", "Newest B", 1, newer_activity),
                KnownChat("chat-c", "No agents", 0, None),
            ]

            all_open = await repo.known_chats(())
            assert [chat.open_sessions for chat in all_open] == [2, 2, 1]
        finally:
            await store.close()

    asyncio.run(main())
