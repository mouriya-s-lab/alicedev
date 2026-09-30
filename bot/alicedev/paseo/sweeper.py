"""Idle sweeper (ARCHITECTURE §4): park conversational agents idle for ``idle_park_seconds``.

Parking releases the agent's runtime and is resumable; it never changes the session state.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from alicedev.config import PluginConfig
    from alicedev.paseo.agent_actor import AgentActor
    from alicedev.store.agents_repo import AgentRow, AgentsRepo

_LOG = logging.getLogger("alicedev.paseo.sweeper")


class IdleSweeper:
    def __init__(
        self,
        *,
        agents: "AgentsRepo",
        actor: "AgentActor",
        config: "PluginConfig",
        is_conversational: Callable[["AgentRow"], Awaitable[bool]],
    ) -> None:
        self._agents = agents
        self._is_conversational = is_conversational
        self._actor = actor
        self._config = config
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="alicedev-sweeper")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._config.sweeper_interval_seconds)
            try:
                await self.sweep_once()
            except Exception:  # noqa: BLE001 - the loop must survive one bad pass
                _LOG.exception("sweeper pass failed")

    async def sweep_once(self) -> int:
        cutoff = self._actor.now() - timedelta(seconds=self._config.idle_park_seconds)
        naive_cutoff = cutoff.replace(tzinfo=None)
        parked = 0
        for agent in await self._agents.idle_candidates(naive_cutoff):
            if not await self._is_conversational(agent):
                continue
            if await self._actor.park_if_idle(agent, older_than=cutoff):
                parked += 1
        if parked:
            _LOG.info("sweeper parked %d idle agent(s)", parked)
        return parked
