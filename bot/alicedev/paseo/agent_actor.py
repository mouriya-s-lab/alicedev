"""Agent state machine executor (ARCHITECTURE §4).

The bot is the only executor. Each ``agent_ref`` has one ``asyncio.Lock`` acting
as a lease: injections into the same agent are strictly serialized, different
agents run concurrently. ``agents.last_activity_at`` is the single idle clock.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Union

from alicedev.ids import new_msg_ref
from alicedev.paseo.control import AgentStatus, PaseoControl, PaseoError
from alicedev.paseo.slots import SlotPool
from alicedev.store.agents_repo import AgentRow, AgentRowStatus

if TYPE_CHECKING:
    from alicedev.config import PluginConfig
    from alicedev.store.agents_repo import AgentsRepo
    from alicedev.store.db import Store
    from alicedev.store.messages_repo import MessagesRepo

_LOG = logging.getLogger("alicedev.paseo.agent_actor")


def build_ingress_command(*, agent_ref: str, msg_ref: str, text: str) -> str:
    """Single-line ``/chat_ingress {json}`` consumed by the omp extension (§5)."""
    payload = json.dumps({"agent": agent_ref, "msg": msg_ref, "text": text}, ensure_ascii=False)
    return f"/chat_ingress {payload}"


@dataclass(frozen=True)
class AgentStarted:
    agent: AgentRow


@dataclass(frozen=True)
class AgentStartFailed:
    reason: str


@dataclass(frozen=True)
class AgentStartDeferred:
    """No online slot was free; nothing was created."""


StartOutcome = Union[AgentStarted, AgentStartFailed, AgentStartDeferred]


class InjectOutcome(str, Enum):
    OK = "ok"
    BUSY = "busy"
    FULL = "full"
    DUPLICATE = "duplicate"
    FAILED = "failed"


class AgentActor:
    def __init__(
        self,
        *,
        store: "Store",
        agents: "AgentsRepo",
        messages: "MessagesRepo",
        paseo: PaseoControl,
        config: "PluginConfig",
    ) -> None:
        self._store = store
        self._agents = agents
        self._messages = messages
        self._paseo = paseo
        self._config = config
        self._locks: dict[str, asyncio.Lock] = {}
        self._pool = SlotPool(
            paseo=paseo, agents=agents, cap=config.max_live_agents, park=self._park_if_free
        )

    def _lock(self, agent_ref: str) -> asyncio.Lock:
        return self._locks.setdefault(agent_ref, asyncio.Lock())

    async def start(
        self,
        *,
        agent_ref: str,
        session_id: int,
        chat_key: str,
        state: str,
        provider: str,
        cwd: str,
        workspace_id: str,
        title: str,
        prompt: str,
        trigger_msg_ref: str | None,
        sender_key: str | None,
        wait_seconds: float = 0.0,
    ) -> StartOutcome:
        """Create the agent once an online slot is free (§4); otherwise create nothing."""
        async with self._pool.admit(wait_seconds=wait_seconds) as admitted:
            if not admitted:
                return AgentStartDeferred()
            await self._agents.create_creating(
                agent_ref=agent_ref, session_id=session_id, state=state, provider=provider
            )
            msg_ref = trigger_msg_ref or new_msg_ref()
            if trigger_msg_ref is not None:
                await self._messages.attach(trigger_msg_ref, agent_ref=agent_ref, text=prompt)
            else:
                await self._messages.claim(
                    msg_ref=msg_ref, chat_key=chat_key, platform_message_id=None,
                    sender_key=sender_key, sender_name=None, content=None, text=prompt,
                    session_id=session_id, agent_ref=agent_ref,
                )
            ingress = build_ingress_command(agent_ref=agent_ref, msg_ref=msg_ref, text=prompt)
            async with self._lock(agent_ref):
                try:
                    handle = await self._paseo.create(
                        agent_ref=agent_ref, provider=provider, cwd=cwd, title=title,
                        initial_prompt=ingress, workspace_id=workspace_id,
                    )
                except PaseoError as exc:
                    _LOG.warning("create failed for %s, trying find_by_label: %s", agent_ref, exc)
                    try:
                        handle = await self._paseo.find_by_label(agent_ref)
                    except PaseoError:
                        handle = None
                    if handle is None:
                        await self._agents.set_status(agent_ref, AgentRowStatus.FAILED)
                        return AgentStartFailed(str(exc))
                await self._agents.set_active(
                    agent_ref,
                    agent_id=handle.agent_id,
                    workspace_id=handle.workspace_id or workspace_id,
                    server_id=handle.server_id,
                )
            row = await self._agents.get(agent_ref)
            assert row is not None
            return AgentStarted(row)

    async def has_room(self) -> bool:
        """Cheap pre-check before a dispatch does any work (the real admission is in ``start``)."""
        return await self._pool.can_admit()

    async def recover_creating(self, agent: AgentRow) -> AgentRowStatus:
        """After a restart: an agent stuck in ``creating`` is found by label or failed."""
        async with self._lock(agent.agent_ref):
            try:
                handle = await self._paseo.find_by_label(agent.agent_ref)
            except PaseoError:
                handle = None
            if handle is None:
                await self._agents.set_status(agent.agent_ref, AgentRowStatus.FAILED)
                return AgentRowStatus.FAILED
            await self._agents.set_active(
                agent.agent_ref, agent_id=handle.agent_id,
                workspace_id=agent.workspace_id, server_id=handle.server_id,
            )
            return AgentRowStatus.ACTIVE

    async def inject(
        self,
        agent: AgentRow,
        *,
        chat_key: str,
        text: str,
        platform_message_id: str | None,
        sender_key: str | None,
        sender_name: str | None,
        content: str | None,
    ) -> InjectOutcome:
        async with self._lock(agent.agent_ref):
            current = await self._agents.get(agent.agent_ref)
            if current is None or not current.agent_id or current.status in (
                AgentRowStatus.ARCHIVED, AgentRowStatus.FAILED, AgentRowStatus.CREATING
            ):
                return InjectOutcome.FAILED
            msg_ref = new_msg_ref()
            claimed = await self._messages.claim(
                msg_ref=msg_ref, chat_key=chat_key, platform_message_id=platform_message_id,
                sender_key=sender_key, sender_name=sender_name, content=content, text=text,
                session_id=current.session_id, agent_ref=current.agent_ref,
            )
            if not claimed.fresh:
                return InjectOutcome.DUPLICATE
            ingress = build_ingress_command(agent_ref=current.agent_ref, msg_ref=msg_ref, text=text)
            try:
                settled = await self._settle(current.agent_id)
                if settled is None:
                    await self._messages.delete(msg_ref)
                    return InjectOutcome.BUSY
                if settled is AgentStatus.CLOSED:
                    # A parked (or archived) agent needs an online slot before it may wake.
                    async with self._pool.admit(exclude=(current.agent_id,)) as admitted:
                        if not admitted:
                            await self._messages.delete(msg_ref)
                            return InjectOutcome.FULL
                        await self._paseo.send(current.agent_id, ingress)
                else:
                    await self._paseo.send(current.agent_id, ingress)
            except PaseoError:
                await self._messages.delete(msg_ref)
                _LOG.exception("inject failed for %s", current.agent_ref)
                return InjectOutcome.FAILED
            await self._agents.touch(current.agent_ref)
            if current.status is AgentRowStatus.CLOSED or settled is AgentStatus.CLOSED:
                await self._agents.set_status(current.agent_ref, AgentRowStatus.ACTIVE)
            return InjectOutcome.OK

    async def _settle(self, agent_id: str) -> AgentStatus | None:
        """The agent's status once it is not mid-turn; ``None`` if it stays busy too long."""
        waited = 0.0
        interval = self._config.inject_poll_seconds
        while True:
            status = await self._paseo.status(agent_id)
            if not status.busy:
                return status
            if waited >= self._config.inject_wait_max_seconds:
                return None
            await asyncio.sleep(interval)
            waited += interval

    async def park_if_idle(self, agent: AgentRow, *, older_than: datetime) -> bool:
        """Sweeper: park an agent that has been idle since before ``older_than``."""
        cutoff = older_than.astimezone(timezone.utc).replace(tzinfo=None) if older_than.tzinfo else older_than
        async with self._lock(agent.agent_ref):
            current = await self._agents.get(agent.agent_ref)
            if current is None or current.status is not AgentRowStatus.ACTIVE or not current.agent_id:
                return False
            last = current.last_activity_at
            if last.tzinfo is not None:
                last = last.astimezone(timezone.utc).replace(tzinfo=None)
            if last >= cutoff:
                return False
            try:
                status = await self._paseo.status(current.agent_id)
                if status.busy:
                    return False
                if status is not AgentStatus.CLOSED:  # already parked or archived elsewhere: only record it
                    await self._paseo.park(current.agent_id)
            except PaseoError:
                _LOG.warning("idle park failed for %s", current.agent_ref, exc_info=True)
                return False
            await self._agents.set_status(current.agent_ref, AgentRowStatus.CLOSED)
            return True

    async def _park_if_free(self, agent: AgentRow) -> bool:
        """Slot pool victim: park an idle agent nobody is using right now.

        Never waits for a lease: a leased agent belongs to a caller that may itself be
        waiting on the pool, so skipping it is the only deadlock-free choice.
        """
        lock = self._lock(agent.agent_ref)
        if lock.locked():
            return False
        async with lock:
            current = await self._agents.get(agent.agent_ref)
            if current is None or not current.agent_id or current.status not in (
                AgentRowStatus.ACTIVE, AgentRowStatus.CLOSED
            ):
                return False
            try:
                if await self._paseo.status(current.agent_id) is not AgentStatus.IDLE:
                    return False
                await self._paseo.park(current.agent_id)
            except PaseoError:
                _LOG.warning("eviction park failed for %s", current.agent_ref, exc_info=True)
                return False
            await self._agents.set_status(current.agent_ref, AgentRowStatus.CLOSED)
            return True

    async def archive(self, agent: AgentRow) -> None:
        async with self._lock(agent.agent_ref):
            if agent.agent_id:
                try:
                    await self._paseo.archive(agent.agent_id)
                except PaseoError:
                    _LOG.warning("paseo archive failed for %s", agent.agent_ref, exc_info=True)
            await self._agents.set_status(agent.agent_ref, AgentRowStatus.ARCHIVED)

    def now(self) -> datetime:
        return datetime.now(timezone.utc)
