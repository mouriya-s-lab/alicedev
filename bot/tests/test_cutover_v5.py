"""Pre-admission cutover invariants over temporary, file-backed DuckDB stores."""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import duckdb
import pytest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))

from alicedev.domain import JsonValue
from alicedev.paseo.control import (
    AgentHandle, AgentStatus, GitResult, LiveAgent, PaseoControl, PaseoError, WorkspaceRef,
)
from alicedev.store.cutover_v5 import run
from alicedev.store.db import Store
from alicedev.store.requirements_repo import (
    NewRequirement, RequirementQuote, RequirementRecord, RequirementSource,
    RequirementSourceKind, RequirementsRepo,
)

STAMP = datetime(2026, 9, 1, 12, 30, 45)
MARKER = "v5_requirements_typed_tools"
TARGETS = ("requirement", "steward", "investigate", "github-issue", "github-pr")
TERMINALS = ("archived", "failed", "main_sync_failed", "done", "rejected")
HISTORY = ("messages", "outbox", "outbound", "favorites", "reports", "tokens_issued")
CUTOVER_TABLES = ("sessions", "agents", "chat_current_sessions", "requirements",
                  "schema_migrations", "schema_version", *HISTORY)


class SourceFailure(Enum):
    NONE = "none"
    INVENTORY = "inventory"
    ARCHIVE = "archive"
    NOT_ARCHIVED = "not_archived"
    INSPECT = "inspect"
    LOOKUP = "lookup"


class CutoverPaseo(PaseoControl):
    """Substitute only Paseo source facts; all persistence uses the real Store."""

    def __init__(self, *, inventory: tuple[str, ...] = (), archived: tuple[str, ...] = ()) -> None:
        self.inventory = list(inventory)
        self.archived = set(archived)
        self.events: list[tuple[str, str | None]] = []
        self.labels: dict[str, str] = {}
        self.lookups: list[str] = []
        self.archives: list[str] = []
        self.confirmations: list[str] = []
        self.failure = SourceFailure.NONE
        self.fail_id = "daemon-second"
        self.observed_store: Store | None = None
        self.expected_before: dict[str, list[tuple]] | None = None

    async def _assert_not_committed(self) -> None:
        if self.observed_store is not None:
            assert await _snapshot(self.observed_store, CUTOVER_TABLES) == self.expected_before

    async def all_agent_ids(self) -> list[str]:
        await self._assert_not_committed()
        self.events.append(("inventory", None))
        if self.failure is SourceFailure.INVENTORY:
            raise PaseoError("source inventory failed")
        return list(self.inventory)

    async def find_by_label(self, agent_ref: str) -> AgentHandle | None:
        await self._assert_not_committed()
        self.events.append(("lookup", agent_ref))
        self.lookups.append(agent_ref)
        if self.failure is SourceFailure.LOOKUP:
            raise PaseoError("source label query failed")
        agent_id = self.labels.get(agent_ref)
        return AgentHandle(agent_id, None, None) if agent_id is not None else None

    async def archive(self, agent_id: str) -> None:
        await self._assert_not_committed()
        self.events.append(("archive", agent_id))
        self.archives.append(agent_id)
        if agent_id == self.fail_id and self.failure is SourceFailure.ARCHIVE:
            raise PaseoError("source archive failed")
        if agent_id != self.fail_id or self.failure is not SourceFailure.NOT_ARCHIVED:
            self.archived.add(agent_id)

    async def is_archived(self, agent_id: str) -> bool:
        await self._assert_not_committed()
        self.events.append(("inspect", agent_id))
        self.confirmations.append(agent_id)
        if agent_id == self.fail_id:
            if self.failure is SourceFailure.INSPECT:
                raise PaseoError("source inspect failed")
        return agent_id in self.archived

    async def status(self, agent_id: str) -> AgentStatus:
        # CLOSED is deliberately available, but is not an archive fact.
        return AgentStatus.CLOSED

    async def live_agents(self) -> list[LiveAgent]:
        raise AssertionError("runtime absence is not archive confirmation")

    async def create(self, *, agent_ref: str, provider: str, cwd: str, title: str,
                     initial_prompt: str, workspace_id: str) -> AgentHandle:
        raise AssertionError("cutover cannot create an agent")

    async def send(self, agent_id: str, text: str) -> None:
        raise AssertionError("cutover cannot wake an agent")

    async def park(self, agent_id: str) -> None:
        raise AssertionError("cutover requires permanent archive")

    async def workspace_local(self, path: str, title: str) -> WorkspaceRef:
        raise AssertionError("cutover must retain workspaces")

    async def worktree_create(self, *, repo: str, base_ref: str, slug: str) -> WorkspaceRef:
        raise AssertionError("cutover cannot create a worktree")

    async def workspace_archive(self, workspace_id: str) -> None:
        raise AssertionError("cutover must retain workspaces")

    async def server_id(self) -> str | None:
        raise AssertionError("cutover does not need server discovery")

    async def git(self, repo: str, *args: str) -> GitResult:
        raise AssertionError("cutover cannot change repositories")


async def _snapshot(store: Store, tables: tuple[str, ...]) -> dict[str, list[tuple]]:
    # Sort complete SQL values rather than comparing storage-file bytes/checkpoints.
    return {table: sorted(await store.fetch_all(f"SELECT * FROM {table}"), key=repr)
            for table in tables}


async def _session(store: Store, session_id: int, *, scenario: str = "requirement",
                   state: str = "discussing", chat: str = "native",
                   value: JsonValue = None, author: str = "telegram:owner") -> None:
    await store.execute(
        "INSERT INTO sessions (session_id, chat_key, no, scenario, name, created_by, input, state, "
        "data, workspace_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, chat, session_id, scenario, "renamed title, not original input", author,
         json.dumps({} if value is None else value, ensure_ascii=False), state,
         '{"history":"retained"}', f"workspace-{session_id}", STAMP, STAMP),
    )


async def _agent(store: Store, session_id: int, ref: str, agent_id: str | None,
                 *, status: str = "active", legacy: str | None = None,
                 at: datetime = STAMP) -> None:
    await store.execute(
        "INSERT INTO agents (agent_ref, session_id, state, provider, agent_id, status, legacy_ref, "
        "workspace_id, created_at, last_activity_at) VALUES (?, ?, 'discussing', 'fixture', ?, ?, ?, ?, ?, ?)",
        (ref, session_id, agent_id, status, legacy, f"workspace-{session_id}", at, at),
    )


async def _current(store: Store, session_id: int, chat: str) -> None:
    await store.execute("INSERT INTO chat_current_sessions VALUES (?, ?, ?)", (chat, session_id, STAMP))


@dataclass(frozen=True)
class OriginalRequirement:
    id: int
    chat: str
    session_ref: str | None
    author: str
    name: str
    text: str
    images: tuple[str, ...] | None
    status: str
    at: datetime

    def restored(self) -> RequirementRecord:
        return RequirementRecord(self.id, self.chat, self.author, self.name, self.text,
                                 self.images or (), None, self.status,
                                 RequirementSource(RequirementSourceKind.V2_REQUIREMENT, str(self.id)), self.at)


ORIGINALS = (
    OriginalRequirement(3, "legacy", "s_requirement", "telegram:1", "甲", "相同原文\n未修剪 ",
                        ("/original/one.png", "https://images.invalid/甲.png"), "reviewed", STAMP),
    OriginalRequirement(11, "legacy", "s_requirement", "telegram:2", "乙", "相同原文\n未修剪 ",
                        (), "fulfilled", datetime(2026, 9, 2, 3, 4, 5)),
    OriginalRequirement(24, "legacy", None, "telegram:3", "孤儿甲", "没有关联会话", None,
                        "open", datetime(2026, 8, 3, 1, 2, 3)),
    OriginalRequirement(40, "other-legacy", "", "telegram:4", "孤儿乙", "空字符串关联", ("/orphan.png",),
                        "cancelled", datetime(2026, 8, 4, 4, 5, 6)),
)


def _original_v2(path: Path) -> None:
    conn = duckdb.connect(str(path))
    try:
        conn.execute((Path(__file__).parent / "fixtures" / "schema_v2.sql").read_text())
        conn.execute("INSERT INTO schema_version (version) VALUES (2)")
        for ref, template, status in (("s_requirement", "requirement", "active"),
                                      ("s_research", "investigate", "closed")):
            conn.execute(
                "INSERT INTO sessions (session_ref, chat_key, template, name, provider, agent_id, status, "
                "created_by, created_at, last_activity_at) VALUES (?, 'legacy', ?, ?, 'fixture', ?, ?, 'owner', ?, ?)",
                (ref, template, "旧标题而非原文", f"daemon-{ref}", status, STAMP, STAMP),
            )
        for value in ORIGINALS:
            conn.execute("INSERT INTO requirements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (value.id, value.chat, value.session_ref, value.author, value.name, value.text,
                          None if value.images is None else json.dumps(value.images, ensure_ascii=False),
                          value.status, value.at))
        conn.execute("INSERT INTO chat_current_sessions VALUES ('legacy', 's_requirement', ?)", (STAMP,))
        conn.execute("INSERT INTO messages VALUES ('m_old', 's_requirement', 'legacy', 'platform-in', "
                     "'original-sender', 'rendered prompt must not become a requirement', ?)", (STAMP,))
        conn.execute("INSERT INTO reply_deliveries VALUES ('reply-old', 's_requirement', '[\"m_old\"]', "
                     "'hash', 'sent', '[\"platform-out\"]', ?, ?)", (STAMP, STAMP))
        conn.execute("INSERT INTO outbound VALUES ('platform-out', 'legacy', 's_requirement', ?)", (STAMP,))
        conn.execute("INSERT INTO favorites VALUES (7, 'legacy', 'saver', '收藏者', 'author', '作者', "
                     "'favorite is not a requirement', '[\"/favorite.png\"]', 'platform-out', ?)", (STAMP,))
        conn.execute("INSERT INTO reports VALUES ('report-old', 's_requirement', '/source.md', '/public.md', ?)", (STAMP,))
        conn.execute("INSERT INTO tokens_issued VALUES ('audit-old', 's_requirement', 'owner', 'alicedev', "
                     "'/history', ?, NULL)", (STAMP,))
    finally:
        conn.close()


def test_original_v2_rows_and_native_input_survive_cutover_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "original-v2.duckdb"
    _original_v2(path)

    async def main() -> None:
        paseo = CutoverPaseo(inventory=("daemon-s_requirement", "daemon-s_research"))
        store = Store(path)
        await store.open()
        try:
            # Two v2 sessions + both NULL/empty orphan records = boundary 4.
            assert await store.fetch_one("SELECT MAX(session_id) FROM sessions") == (4,)
            native_text = "  原始需求\n完整内容与空格  "
            await _session(store, 5, value={"text": native_text, "sender": {"name": "原始发送者"},
                           "images": ["/native.png"], "quoted": {"sender": "被引用的人", "text": "引用原文\n第二行",
                                                                 "images": ["/quoted.png"]}}, state="failed")
            await _session(store, 6, value={"text": native_text, "images": []}, state="queued", author="telegram:fallback")
            await _session(store, 7, value={"sender": {"name": "缺源发送者"}}, state="queued")
            await store.execute("INSERT INTO messages (msg_ref, chat_key, text, session_id, created_at) "
                                "VALUES ('m_missing', 'native', 'AI/rendered substitute is forbidden', 7, ?)", (STAMP,))
            await _current(store, 6, "native")
            retained_tables = (*HISTORY, "v2_sessions", "v2_requirements", "v2_messages", "v2_reply_deliveries")
            history = await _snapshot(store, retained_tables)
            sessions_before = await store.fetch_all("SELECT session_id, input, name, created_at, data FROM sessions ORDER BY session_id")
            terminals_before = await store.fetch_all("SELECT * FROM sessions WHERE state IN ('archived', 'failed') ORDER BY session_id")
            terminal_ids = tuple(row[0] for row in terminals_before)
            report = await run(store, paseo)
            assert (report.skipped, report.legacy_requirements, report.native_requirements, report.missing_originals) == (False, 4, 2, 1)
            repo = RequirementsRepo(store)
            for original in ORIGINALS:
                assert await repo.get(original.id) == original.restored()
            expected_native = (
                RequirementRecord(41, "native", "telegram:owner", "原始发送者", native_text, ("/native.png",),
                                  RequirementQuote(None, "被引用的人", "引用原文\n第二行", ("/quoted.png",)), "open",
                                  RequirementSource(RequirementSourceKind.REQUIREMENT_SESSION, "5"), STAMP),
                RequirementRecord(42, "native", "telegram:fallback", "telegram:fallback", native_text, (), None, "open",
                                  RequirementSource(RequirementSourceKind.REQUIREMENT_SESSION, "6"), STAMP),
            )
            assert (await repo.list_page("native")).items == list(expected_native)
            assert await store.fetch_all("SELECT id FROM requirements ORDER BY id") == [(3,), (11,), (24,), (40,), (41,), (42,)]
            assert await _snapshot(store, retained_tables) == history
            assert await store.fetch_all("SELECT session_id, input, name, created_at, data FROM sessions ORDER BY session_id") == sessions_before
            terminal_marks = ",".join("?" for _ in terminal_ids)
            assert await store.fetch_all(
                f"SELECT * FROM sessions WHERE session_id IN ({terminal_marks}) ORDER BY session_id",
                terminal_ids,
            ) == terminals_before
            assert await store.fetch_all("SELECT * FROM chat_current_sessions") == []
            assert await store.fetch_all("SELECT state FROM sessions WHERE session_id IN (6, 7) ORDER BY session_id") == [("archived",), ("archived",)]
            assert await store.fetch_one("SELECT COUNT(*) FROM schema_migrations WHERE migration = ?", (MARKER,)) == (1,)
            assert await store.fetch_one("SELECT COUNT(*) FROM schema_version WHERE version = 5") == (1,)
            after = await _snapshot(store, CUTOVER_TABLES)
            calls = list(paseo.archives)
            assert (await run(store, paseo)).skipped
            assert await _snapshot(store, CUTOVER_TABLES) == after
            assert paseo.archives == calls
            assert await store.next_id("seq_requirements") == 43
        finally:
            await store.close()
        reopened = Store(path)
        await reopened.open()
        try:
            assert (await run(reopened, paseo)).skipped
            assert await _snapshot(reopened, CUTOVER_TABLES) == after
            saved = await RequirementsRepo(reopened).save(NewRequirement(
                "native", "telegram:new", "新用户", "cutover 后的新需求", (), None,
                RequirementSource(RequirementSourceKind.CHAT_MESSAGE, "new-platform-message")))
            assert saved.record.id == 43
        finally:
            await reopened.close()
        persisted = Store(path)
        await persisted.open()
        try:
            assert (await RequirementsRepo(persisted).get(43)) == saved.record
        finally:
            await persisted.close()

    asyncio.run(main())


def test_all_target_agents_require_source_confirmation_and_upgrade_is_untouched(tmp_path: Path) -> None:
    async def main() -> None:
        store = Store(tmp_path / "all-targets.duckdb")
        await store.open()
        try:
            paseo = CutoverPaseo()
            expected_ids: list[str] = []
            for session_id, (scenario, status) in enumerate(zip(TARGETS, ("active", "closed", "archived", "failed", "creating")), 1):
                chat = f"target-{session_id}"
                await _session(store, session_id, scenario=scenario, state="queued", chat=chat)
                await _current(store, session_id, chat)
                ref = f"a_target_{session_id}"
                daemon_id = f"daemon-target-{session_id}"
                await _agent(store, session_id, ref, None if session_id in (2, 3) else daemon_id,
                             status=status, legacy=f"s_legacy_{session_id}")
                if session_id == 2:
                    paseo.labels[ref] = daemon_id
                    paseo.labels["s_legacy_2"] = "wrong-legacy-agent"
                if session_id == 3:
                    paseo.labels["s_legacy_3"] = daemon_id
                expected_ids.append(daemon_id)
                await _agent(store, session_id, f"a_extra_{session_id}", f"daemon-extra-{session_id}", status="closed",
                             at=datetime(2026, 9, 1, 13))
                expected_ids.append(f"daemon-extra-{session_id}")
            for session_id, (state, scenario) in enumerate(zip(TERMINALS, TARGETS), 6):
                await _session(store, session_id, scenario=scenario, state=state, chat=f"terminal-{session_id}")
                await _current(store, session_id, f"terminal-{session_id}")
                await _agent(store, session_id, f"a_terminal_{session_id}", f"daemon-terminal-{session_id}", status="archived")
                expected_ids.append(f"daemon-terminal-{session_id}")
            await _session(store, 11, scenario="upgrade-bot", state="working", chat="upgrade")
            await _agent(store, 11, "a_upgrade", "daemon-upgrade")
            await _current(store, 11, "upgrade")
            await _session(store, 12, scenario="custom-scenario", chat="unrelated")
            await _agent(store, 12, "a_unrelated", "daemon-unrelated")
            await _current(store, 12, "unrelated")
            paseo.inventory = [*expected_ids, "daemon-upgrade", "daemon-unrelated"]
            terminal_before = await store.fetch_all("SELECT * FROM sessions WHERE session_id BETWEEN 6 AND 10 ORDER BY session_id")
            unaffected_sessions = await store.fetch_all("SELECT * FROM sessions WHERE session_id >= 11 ORDER BY session_id")
            unaffected_agents = await store.fetch_all("SELECT * FROM agents WHERE session_id >= 11 ORDER BY agent_ref")
            paseo.observed_store = store
            paseo.expected_before = await _snapshot(store, CUTOVER_TABLES)
            report = await run(store, paseo)
            assert (report.retired_sessions, report.archived_agents, report.runtime_reset_agents, report.missing_originals) == (5, 15, 0, 2)
            assert paseo.archives == expected_ids
            assert paseo.confirmations == [agent_id for agent_id in expected_ids for _ in range(2)]
            assert paseo.archived == set(expected_ids)
            assert paseo.lookups == ["a_target_2", "a_target_3", "s_legacy_3"]
            assert await store.fetch_all("SELECT agent_ref, agent_id, status FROM agents WHERE session_id = 2 ORDER BY agent_ref") == [
                ("a_extra_2", "daemon-extra-2", "archived"), ("a_target_2", "daemon-target-2", "archived")]
            assert await store.fetch_all("SELECT agent_id, status FROM agents WHERE agent_ref = 'a_target_3'") == [("daemon-target-3", "archived")]
            assert await store.fetch_all("SELECT state FROM sessions WHERE session_id <= 5 ORDER BY session_id") == [("archived",)] * 5
            assert await store.fetch_all("SELECT * FROM sessions WHERE session_id BETWEEN 6 AND 10 ORDER BY session_id") == terminal_before
            assert await store.fetch_all("SELECT * FROM sessions WHERE session_id >= 11 ORDER BY session_id") == unaffected_sessions
            assert await store.fetch_all("SELECT * FROM agents WHERE session_id >= 11 ORDER BY agent_ref") == unaffected_agents
            assert await store.fetch_all("SELECT chat_key, session_id FROM chat_current_sessions ORDER BY chat_key") == [("unrelated", 12), ("upgrade", 11)]
            assert await store.fetch_all("SELECT * FROM requirements") == []
        finally:
            await store.close()

    asyncio.run(main())


async def _failure_fixture(store: Store, *, missing_id: bool = False) -> None:
    await _session(store, 1, scenario="investigate", chat="research")
    await _session(store, 2, value={"text": "native original"}, state="queued", chat="requirements")
    await _agent(store, 1, "a_first", "daemon-first", status="closed")
    await _agent(store, 2, "a_second", None if missing_id else "daemon-second", legacy="s_second", status="archived")
    await _current(store, 1, "research")
    await _current(store, 2, "requirements")


@pytest.mark.parametrize("failure", (SourceFailure.ARCHIVE, SourceFailure.NOT_ARCHIVED, SourceFailure.INSPECT, SourceFailure.LOOKUP))
def test_source_failure_after_an_earlier_archive_does_not_commit_any_cutover_fact(tmp_path: Path, failure: SourceFailure) -> None:
    async def main() -> None:
        path = tmp_path / "source-failure.duckdb"
        store = Store(path)
        await store.open()
        paseo = CutoverPaseo(inventory=("daemon-first", "daemon-second"))
        paseo.failure = failure
        paseo.labels["s_second"] = "daemon-second"
        try:
            await _failure_fixture(store, missing_id=failure is SourceFailure.LOOKUP)
            before = await _snapshot(store, CUTOVER_TABLES)
            sequence_before = await store.fetch_all(
                "SELECT sequence_name, start_value, last_value FROM duckdb_sequences() ORDER BY sequence_name")
            with pytest.raises(PaseoError):
                await run(store, paseo)
            assert paseo.archives[0] == "daemon-first"
            assert paseo.confirmations[0] == "daemon-first"
            assert paseo.events[:4] == [
                ("inventory", None), ("inspect", "daemon-first"),
                ("archive", "daemon-first"), ("inspect", "daemon-first"),
            ]
            assert paseo.archived == {"daemon-first"}
            if failure is SourceFailure.LOOKUP:
                assert paseo.events[4:] == [("lookup", "a_second")]
            elif failure is SourceFailure.INSPECT:
                assert paseo.events[4:] == [("inspect", "daemon-second")]
            elif failure is SourceFailure.ARCHIVE:
                assert paseo.events[4:] == [
                    ("inspect", "daemon-second"), ("archive", "daemon-second"),
                ]
            elif failure is SourceFailure.NOT_ARCHIVED:
                assert paseo.events[4:] == [
                    ("inspect", "daemon-second"), ("archive", "daemon-second"),
                    ("inspect", "daemon-second"),
                ]
            assert await _snapshot(store, CUTOVER_TABLES) == before
            assert await store.fetch_all(
                "SELECT sequence_name, start_value, last_value FROM duckdb_sequences() ORDER BY sequence_name") == sequence_before
            await store.close()
            store = Store(path)
            await store.open()
            assert await _snapshot(store, CUTOVER_TABLES) == before
            prior_archives = list(paseo.archives)
            # Repair only the external source; rerun on the exact same database.
            paseo.failure = SourceFailure.NONE
            report = await run(store, paseo)
            assert (report.retired_sessions, report.archived_agents, report.runtime_reset_agents, report.native_requirements) == (2, 2, 0, 1)
            assert paseo.archives == [*prior_archives, "daemon-second"]
            assert await store.fetch_all("SELECT state FROM sessions ORDER BY session_id") == [("archived",), ("archived",)]
            assert await store.fetch_all("SELECT status FROM agents ORDER BY agent_ref") == [("archived",), ("archived",)]
            assert await store.fetch_all("SELECT * FROM chat_current_sessions") == []
            assert await store.fetch_all("SELECT id, text, source_ref FROM requirements") == [(1, "native original", "2")]
            successful = await _snapshot(store, CUTOVER_TABLES)
        finally:
            await store.close()
        reopened = Store(path)
        await reopened.open()
        try:
            assert (await run(reopened, paseo)).skipped
            assert await _snapshot(reopened, CUTOVER_TABLES) == successful
            assert await reopened.next_id("seq_requirements") == 2
        finally:
            await reopened.close()

    asyncio.run(main())


@pytest.mark.parametrize("failure", (SourceFailure.INVENTORY, SourceFailure.INSPECT))
def test_admission_source_failure_leaves_reopened_store_uncutover(
    tmp_path: Path, failure: SourceFailure,
) -> None:
    async def main() -> None:
        path = tmp_path / "admission-source-failure.duckdb"
        store = Store(path)
        await store.open()
        paseo = CutoverPaseo(inventory=("daemon-first", "daemon-second"))
        paseo.failure = failure
        paseo.fail_id = "daemon-first"
        try:
            await _failure_fixture(store)
            before = await _snapshot(store, CUTOVER_TABLES)
            paseo.observed_store = store
            paseo.expected_before = before
            message = "source inventory failed" if failure is SourceFailure.INVENTORY else "source inspect failed"
            with pytest.raises(PaseoError, match=message):
                await run(store, paseo)
            expected_events: list[tuple[str, str | None]] = [("inventory", None)]
            if failure is SourceFailure.INSPECT:
                expected_events.append(("inspect", "daemon-first"))
            assert paseo.events == expected_events
            assert paseo.archives == []
            assert paseo.archived == set()
            assert await _snapshot(store, CUTOVER_TABLES) == before
        finally:
            await store.close()
        reopened = Store(path)
        await reopened.open()
        try:
            assert await _snapshot(reopened, CUTOVER_TABLES) == before
            assert await reopened.fetch_all("SELECT * FROM requirements") == []
            assert await reopened.fetch_one(
                "SELECT COUNT(*) FROM schema_migrations WHERE migration = ?", (MARKER,),
            ) == (0,)
            assert await reopened.fetch_one("SELECT COUNT(*) FROM schema_version WHERE version = 5") == (0,)
            paseo.observed_store = reopened
            paseo.failure = SourceFailure.NONE
            report = await run(reopened, paseo)
            assert (report.retired_sessions, report.archived_agents, report.runtime_reset_agents, report.native_requirements) == (2, 2, 0, 1)
            assert paseo.archives == ["daemon-first", "daemon-second"]
            assert await reopened.fetch_all("SELECT id, text, source_ref FROM requirements") == [
                (1, "native original", "2"),
            ]
        finally:
            await reopened.close()

    asyncio.run(main())


def test_known_id_absent_from_maintenance_inventory_is_retained_without_inspection(tmp_path: Path) -> None:
    async def main() -> None:
        path = tmp_path / "maintenance-absent-id.duckdb"
        store = Store(path)
        await store.open()
        # The caller's admission-closed maintenance gates establish no old runtime;
        # this inventory describes inspectable source objects, not all history.
        paseo = CutoverPaseo(inventory=("daemon-first", "unrelated-runtime"), archived=("daemon-first",))
        paseo.labels["a_second"] = "different-agent"
        try:
            await _failure_fixture(store)
            history = await _snapshot(store, HISTORY)
            report = await run(store, paseo)
            assert (report.retired_sessions, report.archived_agents, report.runtime_reset_agents, report.native_requirements) == (2, 1, 1, 1)
            assert paseo.events == [("inventory", None), ("inspect", "daemon-first")]
            assert paseo.archives == []
            assert await store.fetch_all("SELECT agent_ref, agent_id, status FROM agents ORDER BY agent_ref") == [
                ("a_first", "daemon-first", "archived"), ("a_second", "daemon-second", "archived"),
            ]
            assert await store.fetch_all("SELECT state FROM sessions ORDER BY session_id") == [
                ("archived",), ("archived",),
            ]
            assert await store.fetch_all("SELECT * FROM chat_current_sessions") == []
            assert await _snapshot(store, HISTORY) == history
            after = await _snapshot(store, CUTOVER_TABLES)
        finally:
            await store.close()
        reopened = Store(path)
        await reopened.open()
        try:
            assert await _snapshot(reopened, CUTOVER_TABLES) == after
            assert await reopened.fetch_all("SELECT text, source_ref FROM requirements") == [("native original", "2")]
            assert (await run(reopened, paseo)).skipped
            assert paseo.events == [("inventory", None), ("inspect", "daemon-first")]
        finally:
            await reopened.close()

    asyncio.run(main())


def test_label_recovered_id_is_inspected_even_when_absent_from_inventory(tmp_path: Path) -> None:
    async def main() -> None:
        store = Store(tmp_path / "label-recovered-id.duckdb")
        await store.open()
        paseo = CutoverPaseo(inventory=("daemon-first",), archived=("daemon-first",))
        paseo.labels["s_second"] = "daemon-second"
        try:
            await _failure_fixture(store, missing_id=True)
            report = await run(store, paseo)
            assert (report.retired_sessions, report.archived_agents, report.runtime_reset_agents, report.native_requirements) == (2, 2, 0, 1)
            assert paseo.events == [
                ("inventory", None), ("inspect", "daemon-first"),
                ("lookup", "a_second"), ("lookup", "s_second"),
                ("inspect", "daemon-second"), ("archive", "daemon-second"),
                ("inspect", "daemon-second"),
            ]
            assert paseo.archived == {"daemon-first", "daemon-second"}
            assert await store.fetch_one("SELECT agent_id, status FROM agents WHERE agent_ref = 'a_second'") == (
                "daemon-second", "archived",
            )
            assert await store.fetch_all("SELECT * FROM chat_current_sessions") == []
            assert await store.fetch_all("SELECT text, source_ref FROM requirements") == [("native original", "2")]
        finally:
            await store.close()

    asyncio.run(main())


def test_explicitly_archived_source_agents_are_not_archived_again(tmp_path: Path) -> None:
    async def main() -> None:
        path = tmp_path / "already-archived.duckdb"
        store = Store(path)
        await store.open()
        paseo = CutoverPaseo(
            inventory=("daemon-first", "daemon-second"), archived=("daemon-first", "daemon-second"),
        )
        try:
            await _failure_fixture(store)
            report = await run(store, paseo)
            assert (report.retired_sessions, report.archived_agents, report.runtime_reset_agents, report.native_requirements) == (2, 2, 0, 1)
            assert paseo.events == [
                ("inventory", None), ("inspect", "daemon-first"), ("inspect", "daemon-second"),
            ]
            assert paseo.archives == []
            assert await store.fetch_all("SELECT agent_id, status FROM agents ORDER BY agent_ref") == [
                ("daemon-first", "archived"), ("daemon-second", "archived"),
            ]
            assert await store.fetch_all("SELECT state FROM sessions ORDER BY session_id") == [
                ("archived",), ("archived",),
            ]
            assert await store.fetch_all("SELECT * FROM chat_current_sessions") == []
            after = await _snapshot(store, CUTOVER_TABLES)
        finally:
            await store.close()
        reopened = Store(path)
        await reopened.open()
        try:
            assert await _snapshot(reopened, CUTOVER_TABLES) == after
            assert (await run(reopened, paseo)).skipped
            assert paseo.archives == []
        finally:
            await reopened.close()

    asyncio.run(main())


def test_backfill_failure_rolls_back_legacy_rows_retirement_and_marker(tmp_path: Path) -> None:
    path = tmp_path / "transaction-failure.duckdb"
    _original_v2(path)

    async def main() -> None:
        store = Store(path)
        await store.open()
        paseo = CutoverPaseo(inventory=("daemon-s_requirement", "daemon-s_research"))
        try:
            await _session(store, 5, value={"text": "native original", "images": [42]})
            await _current(store, 5, "native")
            before = await _snapshot(store, CUTOVER_TABLES)
            with pytest.raises(ValueError, match="images must be a string array"):
                await run(store, paseo)
            assert set(paseo.archives) == {"daemon-s_requirement", "daemon-s_research"}
            assert await _snapshot(store, CUTOVER_TABLES) == before
            await store.execute("UPDATE sessions SET input = ? WHERE session_id = 5", ('{"text":"native original","images":["/fixed.png"]}',))
            report = await run(store, paseo)
            assert (report.legacy_requirements, report.native_requirements) == (4, 1)
            for original in ORIGINALS:
                assert await RequirementsRepo(store).get(original.id) == original.restored()
            native = await RequirementsRepo(store).get(41)
            assert native is not None
            assert (native.text, native.images, native.source.ref) == ("native original", ("/fixed.png",), "5")
            assert await store.next_id("seq_requirements") == 42
            assert await store.fetch_one("SELECT COUNT(*) FROM schema_migrations WHERE migration = ?", (MARKER,)) == (1,)
        finally:
            await store.close()

    asyncio.run(main())


@pytest.mark.parametrize("status", ("creating", "failed"))
def test_successful_current_and_legacy_label_absence_retires_an_idless_row(tmp_path: Path, status: str) -> None:
    async def main() -> None:
        store = Store(tmp_path / "authoritative-absence.duckdb")
        await store.open()
        try:
            await _session(store, 1, value={"text": "original without a created agent"}, state="queued")
            await _agent(store, 1, "a_missing", None, legacy="s_missing", status=status)
            await _current(store, 1, "native")
            paseo = CutoverPaseo()
            report = await run(store, paseo)
            assert (report.retired_sessions, report.archived_agents, report.runtime_reset_agents, report.native_requirements) == (1, 0, 1, 1)
            assert paseo.lookups == ["a_missing", "s_missing"]
            assert paseo.archives == []
            assert paseo.confirmations == []
            assert await store.fetch_one("SELECT state FROM sessions WHERE session_id = 1") == ("archived",)
            assert await store.fetch_one("SELECT agent_id, status FROM agents WHERE agent_ref = 'a_missing'") == (None, "archived")
            assert await store.fetch_all("SELECT * FROM chat_current_sessions") == []
            assert await store.fetch_one("SELECT text FROM requirements") == ("original without a created agent",)
            assert (await run(store, paseo)).skipped
        finally:
            await store.close()

    asyncio.run(main())


@pytest.mark.parametrize("value", ({}, {"text": None}, {"text": ""}, {"text": " \n\t "}))
def test_missing_original_text_is_reported_without_using_title_or_message(tmp_path: Path, value: JsonValue) -> None:
    async def main() -> None:
        store = Store(tmp_path / "missing-input.duckdb")
        await store.open()
        try:
            await _session(store, 1, value=value)
            await store.execute(
                "INSERT INTO messages (msg_ref, chat_key, session_id, text, content, created_at) "
                "VALUES ('m_rendered', 'native', 1, 'rendered AI prompt, not original', NULL, ?)", (STAMP,))
            original_input = await store.fetch_one("SELECT name, input, created_at, data FROM sessions")
            history = await _snapshot(store, HISTORY)
            report = await run(store, CutoverPaseo())
            assert (report.native_requirements, report.missing_originals, report.retired_sessions) == (0, 1, 1)
            assert await store.fetch_all("SELECT * FROM requirements") == []
            assert await store.fetch_one("SELECT name, input, created_at, data FROM sessions") == original_input
            assert await _snapshot(store, HISTORY) == history
            assert (await run(store, CutoverPaseo())).skipped
        finally:
            await store.close()

    asyncio.run(main())
