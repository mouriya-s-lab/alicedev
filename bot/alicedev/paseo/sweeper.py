"""Idle-session sweeper (ARCHITECTURE §4).

Every ``sweeper_interval_seconds`` the sweeper closes ``active`` sessions whose
``last_activity_at`` is older than ``idle_close_seconds`` (12h in production; set
lower in dev/tests). Closing is idempotent and defers to :class:`SessionActor`
so it takes the per-session lease and skips busy agents.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alicedev.config import PluginConfig
    from alicedev.paseo.session_actor import SessionActor
    from alicedev.store.sessions_repo import SessionsRepo

_LOG = logging.getLogger("alicedev.paseo.sweeper")


class IdleSweeper:
    def __init__(
        self,
        *,
        sessions: "SessionsRepo",
        actor: "SessionActor",
        config: "PluginConfig",
    ) -> None:
        self._sessions = sessions
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
        try:
            while True:
                await asyncio.sleep(self._config.sweeper_interval_seconds)
                try:
                    await self.sweep_once()
                except Exception:  # noqa: BLE001 - the loop must survive one bad pass
                    _LOG.exception("sweeper pass failed")
        except asyncio.CancelledError:
            pass

    async def sweep_once(self) -> int:
        cutoff = self._actor.now() - timedelta(seconds=self._config.idle_close_seconds)
        idle = await self._sessions.idle_active(cutoff)
        closed = 0
        for record in idle:
            await self._actor.close(record.session_ref)
            closed += 1
        if closed:
            _LOG.info("sweeper closed %d idle session(s)", closed)
        return closed
