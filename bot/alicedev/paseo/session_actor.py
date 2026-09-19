"""Session state machine executor (ARCHITECTURE §4).

The bot is the only executor of the session state machine. Each ``session_ref``
has one ``asyncio.Lock`` acting as a lease: injections into the same session are
strictly serialized, different sessions run concurrently. Only the bot sees both
directions (injection and ``/v1/reply``), so ``last_activity_at`` is the single
clock and lives in DuckDB.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from alicedev.ids import new_msg_ref, new_session_ref
from alicedev.paseo.control import AgentStatus, PaseoControl, PaseoError
from alicedev.store.sessions_repo import (
    SessionRecord,
    SessionsRepo,
    SessionStatus,
    bounded_session_name,
)

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


@dataclasses.dataclass(frozen=True)
class SessionCreationResult:
    record: SessionRecord
    created: bool


@dataclasses.dataclass
class _InboundLockEntry:
    lock: asyncio.Lock
    references: int = 0


def build_ingress_command(*, session_ref: str, msg_ref: str, text: str) -> str:
    """Single-line ``/chat_ingress {json}`` command consumed by the omp extension."""
    payload = json.dumps(
        {"session": session_ref, "msg": msg_ref, "text": text}, ensure_ascii=False
    )
    return f"/chat_ingress {payload}"


def derive_session_name(template: "PromptTemplate", vars: "TemplateVars") -> str:
    """Derive the stable display name once, before the first prompt is rendered."""
    github = vars.github
    raw_kind = getattr(github, "kind", "") if github is not None else ""
    kind = str(getattr(raw_kind, "value", raw_kind))
    template_name = template.name
    if template_name in {"github-issue", "github_issue"} or kind in {
        "github_issue",
        "issue",
    }:
        number = getattr(github, "number", 0) if github is not None else 0
        title = getattr(github, "title", "") if github is not None else vars.text
        return bounded_session_name(f"Issue #{number} · ", title)
    if template_name in {"github-pr", "github_pr"} or kind in {"github_pr", "pr"}:
        number = getattr(github, "number", 0) if github is not None else 0
        title = getattr(github, "title", "") if github is not None else vars.text
        return bounded_session_name(f"PR #{number} · ", title)
    prefix = {"requirement": "需求 · ", "investigate": "调查 · "}.get(
        template_name, "会话 · "
    )
    return bounded_session_name(prefix, vars.text)


def _with_session_context(
    vars: "TemplateVars", *, session_ref: str, session_name: str
) -> "TemplateVars":
    """Fill correlation and display fields in the frozen render context."""
    return dataclasses.replace(vars, session_ref=session_ref, session_name=session_name)


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
        self._inbound_locks: dict[tuple[str, str], _InboundLockEntry] = {}

    async def start(self) -> None:
        """Lifecycle hook; the transport connects lazily on first use."""
        await self._paseo.connect()

    def _lock_for(self, session_ref: str) -> asyncio.Lock:
        lock = self._locks.get(session_ref)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_ref] = lock
        return lock

    @asynccontextmanager
    async def _inbound_lease(self, chat_key: str, platform_message_id: str):
        key = (chat_key, platform_message_id)
        entry = self._inbound_locks.get(key)
        if entry is None:
            entry = _InboundLockEntry(lock=asyncio.Lock())
            self._inbound_locks[key] = entry
        entry.references += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.references -= 1
            if entry.references == 0 and self._inbound_locks.get(key) is entry:
                del self._inbound_locks[key]

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
    ) -> SessionCreationResult:
        """Create a session once for one inbound platform message."""
        if platform_message_id is None:
            return await self._create_and_inject(
                chat_key=chat_key,
                template=template,
                vars=vars,
                created_by=created_by,
                platform_message_id=None,
                sender_key=sender_key,
            )

        async with self._inbound_lease(chat_key, platform_message_id):
            existing = await self._messages.find_by_platform_message(
                chat_key, platform_message_id
            )
            if existing is not None:
                record = await self._sessions.get(existing.session_ref)
                if record is not None:
                    return SessionCreationResult(record=record, created=False)
                await self._messages.delete(existing.msg_ref)
            return await self._create_and_inject(
                chat_key=chat_key,
                template=template,
                vars=vars,
                created_by=created_by,
                platform_message_id=platform_message_id,
                sender_key=sender_key,
            )

    async def _create_and_inject(
        self,
        *,
        chat_key: str,
        template: "PromptTemplate",
        vars: "TemplateVars",
        created_by: str,
        platform_message_id: str | None,
        sender_key: str | None,
    ) -> SessionCreationResult:
        """Render, claim the inbound message, then create and activate Paseo."""
        session_name = derive_session_name(template, vars)
        session_ref = new_session_ref()
        provider = self._config.paseo_provider
        model = template.model or self._config.paseo_model
        thinking = template.effort or self._config.paseo_thinking
        cwd = template.cwd or self._config.paseo_cwd

        vars_with_context = _with_session_context(
            vars, session_ref=session_ref, session_name=session_name
        )
        rendered = self._templates.render(template, vars_with_context)
        msg_ref = new_msg_ref()
        ingress = build_ingress_command(
            session_ref=session_ref, msg_ref=msg_ref, text=rendered
        )

        if platform_message_id is not None:
            claimed = await self._messages.claim(
                msg_ref=msg_ref,
                session_ref=session_ref,
                chat_key=chat_key,
                platform_message_id=platform_message_id,
                sender_key=sender_key,
                text=rendered,
            )
            if claimed.msg_ref != msg_ref:
                existing = await self._sessions.get(claimed.session_ref)
                if existing is not None:
                    return SessionCreationResult(record=existing, created=False)
                await self._messages.delete(claimed.msg_ref)
                claimed = await self._messages.claim(
                    msg_ref=msg_ref,
                    session_ref=session_ref,
                    chat_key=chat_key,
                    platform_message_id=platform_message_id,
                    sender_key=sender_key,
                    text=rendered,
                )
                if claimed.msg_ref != msg_ref:
                    existing = await self._sessions.get(claimed.session_ref)
                    if existing is not None:
                        return SessionCreationResult(record=existing, created=False)
                    raise SessionActorError("无法领取入站消息，请稍后重试")

        session_created = False
        try:
            await self._sessions.create_creating(
                session_ref=session_ref,
                chat_key=chat_key,
                template=template.name,
                name=session_name,
                created_by=created_by,
                provider=provider,
                model=model,
                thinking=thinking,
            )
            session_created = True
            async with self._lock_for(session_ref):
                try:
                    handle = await self._paseo.create(
                        session_ref=session_ref, provider=provider, model=model,
                        thinking=thinking, cwd=cwd, title=session_name,
                        initial_prompt=ingress,
                    )
                except PaseoError as exc:
                    _LOG.warning(
                        "create failed for %s, trying find_by_label: %s", session_ref, exc
                    )
                    try:
                        handle = await self._paseo.find_by_label(session_ref)
                    except PaseoError:
                        handle = None
                    if handle is None:
                        await self._sessions.set_status(session_ref, SessionStatus.FAILED)
                        raise SessionActorError(
                            "创建会话失败：paseo 暂时不可达，请稍后再试"
                        ) from exc

                await self._sessions.set_active(
                    session_ref,
                    agent_id=handle.agent_id,
                    workspace_id=handle.workspace_id,
                    server_id=handle.server_id,
                    provider=provider,
                    model=model,
                    thinking=thinking,
                )
                if platform_message_id is None:
                    await self._messages.insert(
                        msg_ref=msg_ref,
                        session_ref=session_ref,
                        chat_key=chat_key,
                        platform_message_id=None,
                        sender_key=sender_key,
                        text=rendered,
                    )
                await self._sessions.touch(session_ref)
                await self._sessions.set_current(chat_key, session_ref)
        except Exception:
            if platform_message_id is not None and not session_created:
                await self._messages.delete(msg_ref)
            raise

        record = await self._sessions.get(session_ref)
        assert record is not None
        return SessionCreationResult(record=record, created=True)

    # --- continuation ---------------------------------------------------
    async def inject(
        self,
        *,
        session_ref: str,
        text: str,
        platform_message_id: str | None,
        sender_key: str | None,
        select_current: bool = False,
    ) -> SessionRecord:
        record = await self._sessions.get(session_ref)
        if record is None:
            raise SessionActorError(f"未找到会话 {session_ref}")
        if record.status is SessionStatus.ARCHIVED:
            raise SessionActorError(f"会话 {record.name} 已归档，无法继续")
        record = await self._inject_locked(
            record, text, platform_message_id=platform_message_id, sender_key=sender_key
        )
        if select_current:
            await self._sessions.set_current(record.chat_key, record.session_ref)
        return record

    async def _inject_locked(
        self,
        record: SessionRecord,
        text: str,
        *,
        platform_message_id: str | None,
        sender_key: str | None,
    ) -> SessionRecord:
        async with self._lock_for(record.session_ref):
            current = await self._sessions.get(record.session_ref)
            if current is None:
                raise SessionActorError(f"未找到会话 {record.session_ref}")
            if current.status is SessionStatus.ARCHIVED:
                raise SessionActorError(f"会话 {current.name} 已归档，无法继续")
            if not current.agent_id:
                raise SessionActorError(f"会话 {current.session_ref} 尚未就绪，请稍后再试")
            record = current
            msg_ref = new_msg_ref()
            command = build_ingress_command(
                session_ref=record.session_ref, msg_ref=msg_ref, text=text
            )

            if platform_message_id is not None:
                claimed = await self._messages.claim(
                    msg_ref=msg_ref,
                    session_ref=record.session_ref,
                    chat_key=record.chat_key,
                    platform_message_id=platform_message_id,
                    sender_key=sender_key,
                    text=text,
                )
                if claimed.msg_ref != msg_ref:
                    existing = await self._sessions.get(claimed.session_ref)
                    if existing is not None:
                        return existing
                    await self._messages.delete(claimed.msg_ref)
                    claimed = await self._messages.claim(
                        msg_ref=msg_ref,
                        session_ref=record.session_ref,
                        chat_key=record.chat_key,
                        platform_message_id=platform_message_id,
                        sender_key=sender_key,
                        text=text,
                    )
                    if claimed.msg_ref != msg_ref:
                        existing = await self._sessions.get(claimed.session_ref)
                        if existing is not None:
                            return existing
                        raise SessionActorError("无法领取入站消息，请稍后重试")

            try:
                await self._wait_until_idle(record)
                await self._paseo.send(record.agent_id, command)
            except Exception:
                if platform_message_id is not None:
                    await self._messages.delete(msg_ref)
                raise

            if platform_message_id is None:
                await self._messages.insert(
                    msg_ref=msg_ref,
                    session_ref=record.session_ref,
                    chat_key=record.chat_key,
                    platform_message_id=None,
                    sender_key=sender_key,
                    text=text,
                )
            await self._sessions.touch(record.session_ref)
            if record.status is not SessionStatus.ACTIVE:
                await self._sessions.set_status(record.session_ref, SessionStatus.ACTIVE)
            return record

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

    async def close(self, session_ref: str, *, older_than: datetime) -> bool:
        cutoff = (
            older_than.astimezone(timezone.utc).replace(tzinfo=None)
            if older_than.tzinfo is not None
            else older_than
        )
        async with self._lock_for(session_ref):
            record = await self._sessions.get(session_ref)
            if record is None or record.status is not SessionStatus.ACTIVE:
                return False
            if record.agent_id is None or record.last_activity_at is None:
                return False
            last_activity = record.last_activity_at
            if last_activity.tzinfo is not None:
                last_activity = last_activity.astimezone(timezone.utc).replace(tzinfo=None)
            if last_activity >= cutoff:
                return False
            status = await self._paseo.status(record.agent_id)
            if status.busy:
                return False
            await self._paseo.close(record.agent_id)
            await self._sessions.set_status(session_ref, SessionStatus.CLOSED)
            return True

    async def archive(self, session_ref: str) -> None:
        async with self._lock_for(session_ref):
            record = await self._sessions.get(session_ref)
            if record is None:
                return
            if record.status is SessionStatus.ARCHIVED:
                await self._sessions.clear_current(record.chat_key, expected_ref=session_ref)
                return
            if record.agent_id is not None:
                await self._paseo.archive(record.agent_id)
            await self._sessions.set_status(session_ref, SessionStatus.ARCHIVED)
            await self._sessions.clear_current(record.chat_key, expected_ref=session_ref)

    async def current_for_chat(self, chat_key: str) -> SessionRecord | None:
        return await self._sessions.current_for_chat(chat_key)


    def now(self) -> datetime:
        return datetime.now(timezone.utc)
