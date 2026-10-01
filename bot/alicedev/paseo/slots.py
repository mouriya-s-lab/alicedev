"""Bot live-agent admission pool (ARCHITECTURE §4).

The pool bounds bot-created/woken runtimes using paseo's global visible inventory.
This is not a daemon-wide cap: internal/unplaced runtimes and starts outside the
bot are outside its authority. Both create and wake go through SlotPool.admit;
the slot is held until create/send returns, so concurrent bot requests cannot
both count the same free slot.

The count always comes from paseo (``live_agents``), never from DuckDB, and a
failed query refuses admission. When the cap is reached the pool parks the
least recently active idle agent the bot knows; agents that are running, waiting
for permission, leased by another caller, or unknown to the bot are never touched.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Awaitable, Callable, Collection

from alicedev.paseo.control import AgentStatus, LiveAgent, PaseoControl, PaseoError
from alicedev.store.agents_repo import AgentRow, AgentRowStatus

if TYPE_CHECKING:
    from alicedev.store.agents_repo import AgentsRepo

_LOG = logging.getLogger("alicedev.paseo.slots")

ParkAgent = Callable[[AgentRow], Awaitable[bool]]
_PARKABLE = (AgentRowStatus.ACTIVE, AgentRowStatus.CLOSED)


class SlotPool:
    def __init__(
        self,
        *,
        paseo: PaseoControl,
        agents: "AgentsRepo",
        cap: int,
        park: ParkAgent,
        poll_seconds: float = 2.0,
    ) -> None:
        self._paseo = paseo
        self._agents = agents
        self._cap = cap
        self._park = park
        self._poll = poll_seconds
        self._lock = asyncio.Lock()

    async def _live(self) -> list[LiveAgent] | None:
        try:
            return await self._paseo.live_agents()
        except PaseoError:
            _LOG.warning("live agent query failed; refusing admission", exc_info=True)
            return None

    async def _victims(
        self, live: list[LiveAgent], exclude: Collection[str], tried: set[str]
    ) -> list[AgentRow]:
        idle = [
            a.agent_id for a in live
            if a.status is AgentStatus.IDLE and a.agent_id not in exclude and a.agent_id not in tried
        ]
        rows = [r for r in await self._agents.by_agent_ids(idle) if r.status in _PARKABLE]
        return sorted(rows, key=lambda r: r.last_activity_at)

    async def _admit_locked(self, exclude: Collection[str]) -> bool:
        tried: set[str] = set()
        while True:
            live = await self._live()
            if live is None:
                return False
            if len(live) < self._cap:
                return True
            victims = await self._victims(live, exclude, tried)
            if not victims:
                return False
            victim = victims[0]
            assert victim.agent_id is not None
            tried.add(victim.agent_id)
            await self._park(victim)  # paseo is re-queried next round, whatever the outcome

    async def can_admit(self) -> bool:
        """Non-reserving look: is there a free slot, or an agent that could be parked?"""
        live = await self._live()
        if live is None:
            return False
        if len(live) < self._cap:
            return True
        return bool(await self._victims(live, (), set()))

    @asynccontextmanager
    async def admit(
        self, *, exclude: Collection[str] = (), wait_seconds: float = 0.0
    ) -> AsyncIterator[bool]:
        """Yield ``True`` with the slot held for the body, or ``False`` when none is free.

        ``exclude`` holds paseo agent ids that must not be parked (the requester's own).
        With ``wait_seconds`` the pool retries until the deadline before giving up.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_seconds
        while True:
            await self._lock.acquire()
            try:
                if await self._admit_locked(exclude):
                    yield True
                    return
            finally:
                self._lock.release()
            if loop.time() >= deadline:
                yield False
                return
            await asyncio.sleep(self._poll)
