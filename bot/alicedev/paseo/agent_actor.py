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
from alicedev.paseo.control import PaseoControl, PaseoError
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


StartOutcome = Union[AgentStarted, AgentStartFailed]


class InjectOutcome(str, Enum):
    OK = "ok"
    BUSY = "busy"
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
    ) -> StartOutcome:
        await self._agents.create_creating(
            agent_ref=agent_ref, session_id=session_id, state=state, provider=provider
        )
        msg_ref = trigger_msg_ref or new_msg_ref()
        if trigger_msg_ref is not None:
            await self._messages.attach(trigger_msg_ref, agent_ref=agent_ref, text=prompt)
        else:
            await self._messages.claim(
                msg_ref=msg_ref, chat_key=chat_key, platform_message_id=None,
                sender_key=sender_key, text=prompt, session_id=session_id, agent_ref=agent_ref,
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
                sender_key=sender_key, text=text, session_id=current.session_id,
                agent_ref=current.agent_ref,
            )
            if not claimed.fresh:
                return InjectOutcome.DUPLICATE
            try:
                if not await self._wait_until_idle(current.agent_id):
                    await self._messages.delete(msg_ref)
                    return InjectOutcome.BUSY
                await self._paseo.send(
                    current.agent_id,
                    build_ingress_command(agent_ref=current.agent_ref, msg_ref=msg_ref, text=text),
                )
            except PaseoError:
                await self._messages.delete(msg_ref)
                _LOG.exception("inject failed for %s", current.agent_ref)
                return InjectOutcome.FAILED
            await self._agents.touch(current.agent_ref)
            if current.status is AgentRowStatus.CLOSED:
                await self._agents.set_status(current.agent_ref, AgentRowStatus.ACTIVE)
            return InjectOutcome.OK

    async def _wait_until_idle(self, agent_id: str) -> bool:
        waited = 0.0
        interval = self._config.inject_poll_seconds
        while True:
            status = await self._paseo.status(agent_id)
            if not status.busy:
                return True
            if waited >= self._config.inject_wait_max_seconds:
                return False
            await asyncio.sleep(interval)
            waited += interval

    async def close_if_idle(self, agent: AgentRow, *, older_than: datetime) -> bool:
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
                if (await self._paseo.status(current.agent_id)).busy:
                    return False
                await self._paseo.close(current.agent_id)
            except PaseoError:
                _LOG.warning("idle close failed for %s", current.agent_ref, exc_info=True)
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
