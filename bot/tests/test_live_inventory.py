"""Visible paseo inventory boundaries and bot scheduling admission ownership."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import rt_support  # noqa: F401 - puts bot/ on sys.path

from alicedev.config import PluginConfig
from alicedev.paseo.agent_actor import AgentActor
from alicedev.paseo.cli import CliPaseoControl
from alicedev.paseo.control import AgentStatus, PaseoError
from alicedev.store.agents_repo import AgentsRepo, AgentRowStatus
from alicedev.store.db import Store
from alicedev.store.messages_repo import MessagesRepo
from alicedev.store.sessions_repo import SessionsRepo

CAP = 4


@dataclass
class _InventoryRunner:
    """Fake only the external CLI, including its local versus global visibility."""

    agents: dict[str, AgentStatus]
    local_ids: frozenset[str]
    archived: list[str] = field(default_factory=list)

    async def __call__(self, argv: Sequence[str], timeout: float) -> tuple[int, str, str]:
        if "ls" in argv:
            rows = [
                {"id": agent_id, "status": status.value,
                 "provider": "other-provider", "cwd": "/workspace/other"}
                for agent_id, status in self.agents.items()
                if "-g" in argv or agent_id in self.local_ids
            ]
            return 0, json.dumps(rows), ""
        if "inspect" in argv:
            agent_id = argv[argv.index("inspect") + 1]
            return 0, json.dumps({"Status": self.agents[agent_id].value}), ""
        if "archive" in argv:
            agent_id = argv[-1]
            self.archived.append(agent_id)
            self.agents[agent_id] = AgentStatus.CLOSED
            return 0, "", ""
        raise AssertionError(f"unexpected external CLI operation: {argv!r}")


def _inventory(count: int, status: AgentStatus, *, global_only: bool = False) -> _InventoryRunner:
    agents = {f"visible-agent-{index}": status for index in range(count)}
    local_ids = frozenset() if global_only else frozenset(agents)
    return _InventoryRunner(agents=agents, local_ids=local_ids)


@asynccontextmanager
async def _pool_env(
    tmp_path: Path, runner: _InventoryRunner,
) -> AsyncIterator[tuple[AgentsRepo, AgentActor]]:
    store = Store(tmp_path / "inventory.duckdb")
    await store.open()
    try:
        agents = AgentsRepo(store)
        config = PluginConfig.from_astrbot(
            {"max_live_agents": CAP}, data_dir=tmp_path, plugin_dir=tmp_path,
        )
        actor = AgentActor(
            store=store, agents=agents, messages=MessagesRepo(store),
            paseo=CliPaseoControl(runner=runner), config=config,
        )
        yield agents, actor
    finally:
        await store.close()


@pytest.mark.parametrize("count", [199, 200, 201])
def test_live_inventory_checks_page_boundary_before_filtering_closed(count: int) -> None:
    runner = _inventory(count, AgentStatus.CLOSED)
    control = CliPaseoControl(runner=runner)
    if count < 200:
        assert asyncio.run(control.live_agents()) == []
    else:
        with pytest.raises(PaseoError):
            asyncio.run(control.live_agents())


@pytest.mark.parametrize("count", [199, 200, 201])
def test_closed_inventory_page_boundary_controls_slot_admission(tmp_path: Path, count: int) -> None:
    async def main() -> None:
        runner = _inventory(count, AgentStatus.CLOSED)
        before = dict(runner.agents)
        async with _pool_env(tmp_path, runner) as (agents, actor):
            expected = count < 200
            assert await actor._pool.can_admit() is expected
            async with actor._pool.admit() as admitted:
                assert admitted is expected
            assert await agents.counts() == {}
            assert runner.agents == before
            assert runner.archived == []

    asyncio.run(main())


@pytest.mark.parametrize("count", [CAP - 1, CAP])
@pytest.mark.parametrize("status", [AgentStatus.RUNNING, AgentStatus.IDLE])
def test_unknown_global_runtime_counts_without_becoming_parkable(
    tmp_path: Path, count: int, status: AgentStatus,
) -> None:
    async def main() -> None:
        # A local-only query sees no residents; global visibility changes admission.
        runner = _inventory(count, status, global_only=True)
        before = dict(runner.agents)
        async with _pool_env(tmp_path, runner) as (agents, actor):
            assert await agents.by_agent_ids(tuple(runner.agents)) == []
            assert await actor._pool.can_admit() is (count < CAP)
            async with actor._pool.admit() as admitted:
                assert admitted is (count < CAP)
            assert await agents.counts() == {}
            assert runner.agents == before
            assert runner.archived == []

    asyncio.run(main())


def test_global_unknown_runtime_only_allows_eviction_of_unleased_bot_agent(tmp_path: Path) -> None:
    async def main() -> None:
        runner = _inventory(CAP - 1, AgentStatus.RUNNING, global_only=True)
        unknown = dict(runner.agents)
        runner.agents["bot-agent"] = AgentStatus.IDLE
        async with _pool_env(tmp_path, runner) as (agents, actor):
            async with actor._store.lock:
                session = await SessionsRepo(actor._store).create(
                    chat_key="chat", scenario="investigate", name="known bot agent",
                    created_by="user", input={}, state="discussing", assigned_by=None,
                )
            await agents.create_creating(
                agent_ref="a_known", session_id=session.session_id,
                state="discussing", provider="omp-alicedev",
            )
            await agents.set_active(
                "a_known", agent_id="bot-agent", workspace_id="workspace", server_id="server",
            )
            before = await agents.get("a_known")
            assert before is not None and before.status is AgentRowStatus.ACTIVE

            # Use the actor's real lease and parking callback, not a copied eviction rule.
            async with actor._lock("a_known"):
                async with actor._pool.admit() as admitted:
                    assert admitted is False
                assert await agents.get("a_known") == before
                assert runner.archived == []

            async with actor._pool.admit() as admitted:
                assert admitted is True
            after = await agents.get("a_known")
            assert after is not None and after.status is AgentRowStatus.CLOSED
            assert runner.archived == ["bot-agent"]
            assert runner.agents == {**unknown, "bot-agent": AgentStatus.CLOSED}
            assert await agents.by_agent_ids(tuple(unknown)) == []

    asyncio.run(main())
