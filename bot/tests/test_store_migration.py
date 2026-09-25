"""Schema v4: fresh open, v2 → v4 migration on a synthetic fixture, and the prod-copy rehearsal."""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import datetime
from pathlib import Path

import duckdb
import pytest
import rt_support  # noqa: F401 - puts bot/ on sys.path

from alicedev.store.agents_repo import AgentsRepo
from alicedev.store.db import SCHEMA_VERSION, Store
from alicedev.store.sessions_repo import SessionsRepo

FIXTURES = Path(__file__).parent / "fixtures"
PROD_COPY = Path(os.environ.get("ALICEDEV_PROD_DB_COPY",
                                str(Path.home() / "Ext/tmp/alicedev-migration/alicedev.duckdb")))


def test_fresh_open_creates_v4(tmp_path: Path) -> None:
    async def main() -> None:
        store = Store(str(tmp_path / "db.duckdb"))
        await store.open()
        assert (await store.fetch_one("SELECT MAX(version) FROM schema_version"))[0] == SCHEMA_VERSION
        async with store.lock:
            row = await SessionsRepo(store).create(chat_key="c", scenario="requirement", name="x",
                                                   assigned_by=None, created_by="u", input={}, state="queued")
        assert (row.no, row.session_id) == (1, 1)
        await store.close()
        store = Store(str(tmp_path / "db.duckdb"))
        await store.open()  # reopen is idempotent
        async with store.lock:
            row = await SessionsRepo(store).create(chat_key="c", scenario="requirement", name="y",
                                                   assigned_by=None, created_by="u", input={}, state="queued")
        assert (row.no, row.session_id) == (2, 2)
        await store.close()

    asyncio.run(main())


def _v2_fixture(path: Path) -> None:
    conn = duckdb.connect(str(path))
    conn.execute((FIXTURES / "schema_v2.sql").read_text())
    conn.execute("INSERT INTO schema_version (version) VALUES (2)")
    t = [datetime(2026, 9, 1, h) for h in range(10)]
    sessions = [
        ("s_aaa", "chatA", "requirement", "需求 · 甲", "omp-alicedev", "muse", "ag-1", "ws-1", "srv", "active", "u1", t[1]),
        ("s_bbb", "chatA", "investigate", "", "omp-alicedev", None, "ag-2", "ws-2", "srv", "archived", "u2", t[2]),
        ("s_ccc", "chatB", "github-issue", "Issue #3", "omp-alicedev", None, "ag-3", None, None, "closed", "u1", t[3]),
        ("s_ddd", "chatA", "requirement", "坏", "omp-alicedev", None, None, None, None, "creating", "u1", t[4]),
    ]
    for s in sessions:
        conn.execute(
            "INSERT INTO sessions (session_ref, chat_key, template, name, provider, model, agent_id, "
            "workspace_id, server_id, status, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", s)
    conn.execute("INSERT INTO requirements (id, chat_key, session_ref, author_key, author_name, text, created_at) "
                 "VALUES (1, 'chatA', 's_aaa', 'u1', 'U1', '甲的需求', ?), "
                 "(2, 'chatA', NULL, 'u3', 'U3', '孤儿需求', ?)", (t[1], t[0]))
    conn.execute("INSERT INTO chat_current_sessions VALUES ('chatA', 's_aaa', ?), ('chatB', 's_ccc', ?)", (t[5], t[5]))
    conn.execute("INSERT INTO messages VALUES ('m_1', 's_aaa', 'chatA', 'p1', 'u1', 'hi', ?)", (t[1],))
    conn.execute("INSERT INTO reply_deliveries VALUES ('r1', 's_aaa', '[]', 'h', 'sent', '[\"x\"]', ?, ?), "
                 "('r2', 's_ccc', '[]', 'h2', 'failed', NULL, ?, ?)", (t[2], t[2], t[3], t[3]))
    conn.execute("INSERT INTO outbound VALUES ('x', 'chatA', 's_aaa', ?)", (t[2],))
    conn.execute("INSERT INTO favorites (id, chat_key, saver_key, saver_name, author_key, author_name, text) "
                 "VALUES (7, 'chatA', 'u1', 'U1', 'u2', 'U2', 'fav')")
    conn.execute("INSERT INTO reports VALUES ('rep1', 's_bbb', '/a', '/b', ?)", (t[2],))
    conn.execute("INSERT INTO tokens_issued VALUES ('tok1', 's_aaa', 'u1', 'alicedev', '/h/x', ?, NULL)", (t[2],))
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_requirements START 3")
    conn.close()


def test_migrate_synthetic_v2(tmp_path: Path) -> None:
    db = tmp_path / "v2.duckdb"
    _v2_fixture(db)

    async def main() -> None:
        store = Store(str(db))
        await store.open()
        rows = await store.fetch_all(
            "SELECT chat_key, no, scenario, name, state, input FROM sessions ORDER BY chat_key, no")
        assert [(r[0], r[1], r[2], r[4]) for r in rows] == [
            ("chatA", 1, "requirement", "archived"),  # orphan requirement (t0)
            ("chatA", 2, "requirement", "discussing"),  # s_aaa
            ("chatA", 3, "investigate", "archived"),  # s_bbb
            ("chatA", 4, "requirement", "failed"),  # s_ddd creating
            ("chatB", 1, "github-issue", "discussing"),  # s_ccc closed → resumable
        ]
        assert rows[0][3] == "需求 · 孤儿需求" and '"孤儿需求"' in rows[0][5]
        assert rows[2][3].startswith("历史会话 · ")
        agents = AgentsRepo(store)
        a = await agents.get("s_aaa")  # legacy ref still resolves
        assert a is not None and a.agent_ref.startswith("a_") and a.agent_id == "ag-1"
        assert (a.state, a.status.value, a.provider) == ("discussing", "active", "omp-alicedev/muse")
        c = await agents.get("s_ccc")
        assert c.status.value == "closed"
        assert (await agents.get("s_ddd")) is None  # no agent_id → no agent row
        cur = await store.fetch_all("SELECT chat_key, session_id FROM chat_current_sessions ORDER BY 1")
        assert [r[0] for r in cur] == ["chatA", "chatB"]
        out = await store.fetch_all("SELECT reply_id, state, session_id FROM outbox ORDER BY seq")
        assert [(r[0], r[1]) for r in out] == [("r1", "sent"), ("r2", "failed")]
        assert (await store.fetch_one("SELECT agent_ref FROM messages WHERE msg_ref = 'm_1'"))[0] == a.agent_ref
        assert (await store.fetch_one("SELECT session_id FROM reports"))[0] is not None
        assert (await store.fetch_one("SELECT session_id FROM tokens_issued"))[0] == a.session_id
        assert (await store.fetch_one("SELECT COUNT(*) FROM favorites"))[0] == 1
        assert (await store.fetch_one("SELECT COUNT(*) FROM v2_sessions"))[0] == 4
        # Sequences continue after migrated ids.
        async with store.lock:
            new = await SessionsRepo(store).create(chat_key="chatB", scenario="requirement", name="n",
                                                   assigned_by=None, created_by="u", input={}, state="queued")
        assert (new.session_id, new.no) == (6, 2)
        assert await store.next_id("seq_favorites") == 8
        await store.close()
        # Second open: migration does not run again.
        store = Store(str(db))
        await store.open()
        assert (await store.fetch_one("SELECT COUNT(*) FROM sessions"))[0] == 6
        await store.close()

    asyncio.run(main())


@pytest.mark.skipif(not PROD_COPY.exists(), reason="prod DB copy not present")
def test_migrate_prod_copy_rehearsal(tmp_path: Path) -> None:
    """Rehearse on a *copy* of the prod DB; the source file is never opened for writing."""
    db = tmp_path / "alicedev.duckdb"
    shutil.copy2(PROD_COPY, db)
    wal = PROD_COPY.with_name(PROD_COPY.name + ".wal")
    if wal.exists():
        shutil.copy2(wal, db.with_name(db.name + ".wal"))

    src = duckdb.connect(str(db))
    before = {t: src.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in (
        "sessions", "requirements", "messages", "reply_deliveries", "outbound",
        "chat_current_sessions", "favorites", "reports", "tokens_issued")}
    orphans = src.execute("SELECT COUNT(*) FROM requirements WHERE session_ref IS NULL").fetchone()[0]
    with_agent = src.execute("SELECT COUNT(*) FROM sessions WHERE agent_id IS NOT NULL").fetchone()[0]
    src.close()

    async def main() -> None:
        store = Store(str(db))
        await store.open()
        q = lambda sql: store.fetch_one(sql)  # noqa: E731
        assert (await q("SELECT COUNT(*) FROM sessions"))[0] == before["sessions"] + orphans
        assert (await q("SELECT COUNT(*) FROM agents"))[0] == with_agent
        assert (await q("SELECT COUNT(*) FROM agents WHERE legacy_ref IS NOT NULL"))[0] == with_agent
        assert (await q("SELECT COUNT(*) FROM messages"))[0] == before["messages"]
        assert (await q("SELECT COUNT(*) FROM outbox"))[0] == before["reply_deliveries"]
        assert (await q("SELECT COUNT(*) FROM outbox WHERE state = 'queued'"))[0] == 0  # never resent
        for t in ("outbound", "favorites", "reports", "tokens_issued"):
            assert (await q(f"SELECT COUNT(*) FROM {t}"))[0] == before[t], t
        assert (await q("SELECT COUNT(*) FROM chat_current_sessions"))[0] <= before["chat_current_sessions"]
        assert (await q("SELECT MAX(version) FROM schema_version"))[0] == SCHEMA_VERSION
        dup = await q("SELECT COUNT(*) FROM (SELECT chat_key, no FROM sessions GROUP BY ALL HAVING COUNT(*) > 1)")
        assert dup[0] == 0
        unknown = await store.fetch_all(
            "SELECT DISTINCT scenario FROM sessions WHERE scenario NOT IN "
            "('requirement', 'investigate', 'github-issue', 'github-pr', 'upgrade-bot')")
        assert unknown == []
        await store.close()
        store = Store(str(db))
        await store.open()
        assert (await store.fetch_one("SELECT COUNT(*) FROM sessions"))[0] == before["sessions"] + orphans
        await store.close()

    asyncio.run(main())
