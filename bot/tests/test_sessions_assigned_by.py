"""Session assignment provenance persistence."""

from __future__ import annotations

import asyncio
from pathlib import Path

import rt_support  # noqa: F401 - puts bot/ on sys.path

from alicedev.store.db import Store
from alicedev.store.sessions_repo import SessionsRepo


def test_assigned_by_round_trips_through_create_get_and_by_no(tmp_path: Path) -> None:
    async def main() -> None:
        store = Store(str(tmp_path / "sessions.duckdb"))
        await store.open()
        try:
            sessions = SessionsRepo(store)
            async with store.lock:
                unassigned = await sessions.create(
                    chat_key="chat",
                    scenario="requirement",
                    name="unassigned",
                    created_by="user",
                    input={},
                    state="queued",
                    assigned_by=None,
                )
                assigned = await sessions.create(
                    chat_key="chat",
                    scenario="requirement",
                    name="assigned",
                    created_by="user",
                    input={},
                    state="queued",
                    assigned_by=73,
                )

            assert unassigned.assigned_by is None
            assert assigned.assigned_by == 73

            got_unassigned = await sessions.get(unassigned.session_id)
            got_assigned = await sessions.get(assigned.session_id)
            assert got_unassigned is not None
            assert got_assigned is not None
            assert got_unassigned.assigned_by is None
            assert got_assigned.assigned_by == 73

            numbered_unassigned = await sessions.by_no(unassigned.chat_key, unassigned.no)
            numbered_assigned = await sessions.by_no(assigned.chat_key, assigned.no)
            assert numbered_unassigned is not None
            assert numbered_assigned is not None
            assert numbered_unassigned.assigned_by is None
            assert numbered_assigned.assigned_by == 73
        finally:
            await store.close()

    asyncio.run(main())
