"""Session state machine executor (ARCHITECTURE §4).

The bot is the only executor of the session state machine. Each ``session_ref``
has one ``asyncio.Lock`` acting as a lease: injections into the same session are
strictly serialized, different sessions run concurrently. Only the bot sees both
directions (injection and ``/v1/reply``), so ``last_activity_at`` is the single
clock and lives in DuckDB.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from alicedev.ids import new_msg_ref, new_session_ref
from alicedev.paseo.control import AgentStatus, PaseoControl, PaseoError
from alicedev.store.sessions_repo import SessionRecord, SessionsRepo, SessionStatus

if TYPE_CHECKING:
    from alicedev.config import PluginConfig
    from alicedev.store.db import Store
    from alicedev.store.messages_repo import MessagesRepo
    from alicedev.templates.registry import PromptTemplate, TemplateRegistry, TemplateVars

_LOG = logging.getLogger("alicedev.paseo.session_actor")


class SessionActorError(RuntimeError):
    pass


class InjectBusyTimeout(SessionActorError):
    """The target agent stayed busy past the injection wait ceiling."""


def build_ingress_command(*, session_ref: str, msg_ref: str, text: str) -> str:
    """Single-line ``/chat_ingress {json}`` command consumed by the omp extension."""
    payload = json.dumps(
        {"session": session_ref, "msg": msg_ref, "text": text}, ensure_ascii=False
    )
    return f"/chat_ingress {payload}"


def _with_session_ref(vars: "TemplateVars", session_ref: str):
    """Return a copy of ``vars`` with ``session_ref`` filled in (frozen dataclass)."""
    import dataclasses

    return dataclasses.replace(vars, session_ref=session_ref)


class SessionActor:
    def __init__(
        self,
        *,
        store: "Store",
        sessions: SessionsRepo,
        messages: "MessagesRepo",
        templates: "TemplateRegistry",
        paseo: PaseoControl,
        config: "PluginConfig",
    ) -> None:
        self._store = store
        self._sessions = sessions
        self._messages = messages
        self._templates = templates
        self._paseo = paseo
        self._config = config
        self._locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        """Lifecycle hook; the transport connects lazily on first use."""
        await self._paseo.connect()

    def _lock_for(self, session_ref: str) -> asyncio.Lock:
        lock = self._locks.get(session_ref)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_ref] = lock
        return lock

    # --- creation -------------------------------------------------------

    async def create_and_inject(
        self,
        *,
        chat_key: str,
        template: "PromptTemplate",
        vars: "TemplateVars",
        created_by: str,
        platform_message_id: str | None,
        sender_key: str | None,
    ) -> SessionRecord:
        """Open a session (creating -> active); the rendered first turn rides on
        ``create`` as the initial prompt (§4 spike: MCP create requires it). The
        ingress command wraps it so the omp extension intercepts it before the
        model."""
        session_ref = new_session_ref()
        provider = self._config.paseo_provider
        model = template.model or self._config.paseo_model
        thinking = template.effort or self._config.paseo_thinking
        cwd = template.cwd or self._config.paseo_cwd

        vars_with_ref = _with_session_ref(vars, session_ref)
        rendered = self._templates.render(template, vars_with_ref)
        msg_ref = new_msg_ref()
        ingress = build_ingress_command(
            session_ref=session_ref, msg_ref=msg_ref, text=rendered
        )

        await self._sessions.create_creating(
            session_ref=session_ref, chat_key=chat_key, template=template.name,
            created_by=created_by, provider=provider, model=model, thinking=thinking,
        )
        async with self._lock_for(session_ref):
            try:
                handle = await self._paseo.create(
                    session_ref=session_ref, provider=provider, model=model,
                    thinking=thinking, cwd=cwd, title=f"alicedev {session_ref}",
                    initial_prompt=ingress,
                )
            except PaseoError as exc:
                _LOG.warning("create failed for %s, trying find_by_label: %s", session_ref, exc)
                try:
                    handle = await self._paseo.find_by_label(session_ref)
                except PaseoError:
                    handle = None
                if handle is None:
                    await self._sessions.set_status(session_ref, SessionStatus.FAILED)
                    raise SessionActorError("创建会话失败：paseo 暂时不可达，请稍后再试") from exc

            await self._sessions.set_active(
                session_ref, agent_id=handle.agent_id, workspace_id=handle.workspace_id,
                server_id=handle.server_id, provider=provider, model=model, thinking=thinking,
            )
            await self._messages.insert(
                msg_ref=msg_ref, session_ref=session_ref, chat_key=chat_key,
                platform_message_id=platform_message_id, sender_key=sender_key, text=rendered,
            )
            await self._sessions.touch(session_ref)
        record = await self._sessions.get(session_ref)
        assert record is not None
        return record

    # --- continuation ---------------------------------------------------

    async def inject(
        self,
        *,
        session_ref: str,
        text: str,
        platform_message_id: str | None,
        sender_key: str | None,
    ) -> SessionRecord:
        record = await self._sessions.get(session_ref)
        if record is None:
            raise SessionActorError(f"未找到会话 {session_ref}")
        await self._inject_locked(
            record, text, platform_message_id=platform_message_id, sender_key=sender_key
        )
        return record

    async def _inject_locked(
        self,
        record: SessionRecord,
        text: str,
        *,
        platform_message_id: str | None,
        sender_key: str | None,
    ) -> None:
        if not record.agent_id:
            raise SessionActorError(f"会话 {record.session_ref} 尚未就绪，请稍后再试")
        async with self._lock_for(record.session_ref):
            await self._wait_until_idle(record)
            msg_ref = new_msg_ref()
            command = build_ingress_command(
                session_ref=record.session_ref, msg_ref=msg_ref, text=text
            )
            await self._paseo.send(record.agent_id, command)
            await self._messages.insert(
                msg_ref=msg_ref, session_ref=record.session_ref, chat_key=record.chat_key,
                platform_message_id=platform_message_id, sender_key=sender_key, text=text,
            )
            await self._sessions.touch(record.session_ref)
            if record.status is not SessionStatus.ACTIVE:
                await self._sessions.set_status(record.session_ref, SessionStatus.ACTIVE)

    async def _wait_until_idle(self, record: SessionRecord) -> None:
        agent_id = record.agent_id
        assert agent_id is not None
        waited = 0.0
        interval = self._config.inject_poll_seconds
        ceiling = self._config.inject_wait_max_seconds
        while True:
            status = await self._paseo.status(agent_id)
            if not status.busy:
                return
            if waited >= ceiling:
                raise InjectBusyTimeout(record.session_ref)
            await asyncio.sleep(interval)
            waited += interval

    # --- lifecycle actions ---------------------------------------------

    async def close(self, session_ref: str) -> None:
        record = await self._sessions.get(session_ref)
        if record is None or record.agent_id is None:
            return
        async with self._lock_for(session_ref):
            status = await self._paseo.status(record.agent_id)
            if status.busy:
                return
            await self._paseo.close(record.agent_id)
            await self._sessions.set_status(session_ref, SessionStatus.CLOSED)

    async def archive(self, session_ref: str) -> None:
        record = await self._sessions.get(session_ref)
        if record is None or record.agent_id is None:
            return
        async with self._lock_for(session_ref):
            await self._paseo.archive(record.agent_id)
            await self._sessions.set_status(session_ref, SessionStatus.ARCHIVED)

    async def latest_active_for_chat(self, chat_key: str) -> SessionRecord | None:
        return await self._sessions.latest_active_for_chat(chat_key)

    def now(self) -> datetime:
        return datetime.now(timezone.utc)
