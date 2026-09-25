"""Exercise the in-place v3 to v4 migration against the historical v3 schema."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime
from pathlib import Path

import duckdb
import rt_support  # noqa: F401 - puts bot/ on sys.path

from alicedev.store.db import Store

ROOT = Path(__file__).resolve().parents[2]
V3_SCHEMA_REVISION = "b042244"


def _create_v3_database(path: Path) -> None:
    schema = subprocess.run(
        ["git", "show", f"{V3_SCHEMA_REVISION}:bot/alicedev/store/schema.sql"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    conn = duckdb.connect(str(path))
    try:
        conn.execute(schema)
        conn.execute("INSERT INTO schema_version (version) VALUES (3)")
        conn.execute(
            "INSERT INTO sessions "
            "(session_id, chat_key, no, scenario, name, created_by, input, state, "
            "workspace_id, worktree_path, base_sha, data, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, CAST(? AS JSON), ?, ?, ?, ?, CAST(? AS JSON), ?, ?)",
            (
                41,
                "telegram:GroupMessage:-100",
                7,
                "requirement",
                "preserved session",
                "telegram:7",
                '{"text":"preserved input"}',
                "discussing",
                "workspace-41",
                "/worktree/41",
                "abcdef0",
                '{"marker":"preserved data"}',
                datetime(2026, 9, 20, 12, 30),
                datetime(2026, 9, 20, 12, 35),
            ),
        )
        conn.execute(
            "INSERT INTO messages "
            "(msg_ref, agent_ref, chat_key, platform_message_id, sender_key, text, session_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "m_v3_migration",
                "a_existing",
                "telegram:GroupMessage:-100",
                "platform-v3-1",
                "telegram:7",
                "preserved injected text",
                41,
                datetime(2026, 9, 20, 12, 31),
            ),
        )
    finally:
        conn.close()


async def _assert_v4_state(store: Store) -> None:
    assert await store.fetch_one("SELECT COUNT(*) FROM schema_version WHERE version = 4") == (1,)
    session = await store.fetch_one(
        "SELECT session_id, chat_key, no, scenario, name, created_by, "
        "json_extract_string(input, '$.text'), state, workspace_id, worktree_path, base_sha, "
        "json_extract_string(data, '$.marker'), created_at, updated_at, assigned_by "
        "FROM sessions WHERE session_id = 41"
    )
    assert session == (
        41,
        "telegram:GroupMessage:-100",
        7,
        "requirement",
        "preserved session",
        "telegram:7",
        "preserved input",
        "discussing",
        "workspace-41",
        "/worktree/41",
        "abcdef0",
        "preserved data",
        datetime(2026, 9, 20, 12, 30),
        datetime(2026, 9, 20, 12, 35),
        None,
    )

    message = await store.fetch_one(
        "SELECT msg_ref, agent_ref, chat_key, platform_message_id, sender_key, text, session_id, "
        "created_at, sender_name, content FROM messages WHERE msg_ref = 'm_v3_migration'"
    )
    assert message == (
        "m_v3_migration",
        "a_existing",
        "telegram:GroupMessage:-100",
        "platform-v3-1",
        "telegram:7",
        "preserved injected text",
        41,
        datetime(2026, 9, 20, 12, 31),
        None,
        None,
    )


def test_v3_database_migrates_to_v4_and_reopen_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "v3.duckdb"
    _create_v3_database(db_path)

    async def main() -> None:
        for _ in range(2):
            store = Store(db_path)
            try:
                await store.open()
                await _assert_v4_state(store)
            finally:
                await store.close()

    asyncio.run(main())
