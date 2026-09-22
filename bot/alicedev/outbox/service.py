"""Outbound queue service (ARCHITECTURE §6): enqueue and the outbox worker.

Enqueue never touches the platform. The worker delivers, per chat, the oldest
due row first; a row that fails keeps blocking its chat (FIFO) until it is sent
or runs out of retries. Delivery is at-least-once: a crash after the platform
send and before ``sent`` is recorded resends the row.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence

from alicedev.outbox.render import RenderError
from alicedev.store.outbox_repo import OutboxRepo, OutboxState

if TYPE_CHECKING:
    from alicedev.outbox.render import OutboxRenderer
    from alicedev.store.db import Store

_LOG = logging.getLogger("alicedev.outbox")


class PlatformSender(Protocol):
    async def send(self, chat_key: str, components: list[Any]) -> list[str]:
        """Send a chain proactively; return platform message ids (may be empty)."""
        ...


def digest(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                           default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Outbox:
    def __init__(
        self,
        *,
        store: "Store",
        renderer: "OutboxRenderer",
        sender: PlatformSender,
        poll_seconds: float = 1.0,
    ) -> None:
        self._store = store
        self._repo = OutboxRepo(store)
        self._renderer = renderer
        self._sender = sender
        self._poll = poll_seconds
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def repo(self) -> OutboxRepo:
        return self._repo

    async def enqueue(
        self,
        chat_key: str,
        payload: Mapping[str, Any],
        *,
        session_id: int | None = None,
    ) -> str:
        """Queue a bot-originated message (program replies, scheduler notices)."""
        reply_id = f"sys_{uuid.uuid4().hex}"
        async with self._store.lock:
            await self._repo.insert(
                reply_id=reply_id, chat_key=chat_key, session_id=session_id, agent_ref=None,
                msgs=(), payload=payload, payload_sha256=digest(payload),
            )
        self.wake()
        return reply_id

    async def enqueue_text(self, chat_key: str, text: str, *, session_id: int | None = None) -> None:
        if text.strip():
            await self.enqueue(chat_key, {"type": "text", "text": text}, session_id=session_id)

    async def insert_ai_locked(
        self,
        *,
        reply_id: str,
        chat_key: str,
        session_id: int,
        agent_ref: str,
        msgs: Sequence[str],
        payload: Mapping[str, Any],
        payload_sha256: str,
        deliver: bool = True,
    ) -> None:
        """Insert an agent reply row; caller holds ``store.lock`` (§6 transaction)."""
        await self._repo.insert(
            reply_id=reply_id, chat_key=chat_key, session_id=session_id, agent_ref=agent_ref,
            msgs=msgs, payload=payload, payload_sha256=payload_sha256,
            state=OutboxState.QUEUED if deliver else OutboxState.SENT,
        )

    def wake(self) -> None:
        self._wake.set()

    # --- worker -----------------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="alicedev-outbox")

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
            try:
                await self.drain_once()
            except Exception:  # noqa: BLE001 - the worker must survive one bad pass
                _LOG.exception("outbox pass failed")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._poll)
            except asyncio.TimeoutError:
                pass

    async def drain_once(self) -> int:
        """Deliver every chat's due head row once; return how many were sent."""
        sent = 0
        for row in await self._repo.due_heads():
            try:
                components = await self._renderer.render(row.payload)
            except RenderError as exc:
                _LOG.error("outbox %s unrenderable: %s", row.reply_id, exc)
                await self._fail_forever(row.reply_id, str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 - e.g. t2i down: retry later
                _LOG.warning("outbox %s render failed: %s", row.reply_id, exc)
                await self._repo.mark_attempt_failed(row, f"render: {exc}")
                continue
            try:
                ids = await self._sender.send(row.chat_key, components)
            except Exception as exc:  # noqa: BLE001 - adapter failure: retry with backoff
                state = await self._repo.mark_attempt_failed(row, f"send: {exc}")
                _LOG.warning("outbox %s send failed (%s): %s", row.reply_id, state.value, exc)
                continue
            async with self._store.lock:
                await self._repo.mark_sent(row.reply_id, ids)
                for pmid in ids:
                    await self._store.execute(
                        "INSERT INTO outbound (platform_message_id, chat_key, session_id) "
                        "VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                        (pmid, row.chat_key, row.session_id),
                    )
            sent += 1
        return sent

    async def _fail_forever(self, reply_id: str, error: str) -> None:
        async with self._store.lock:
            await self._store.execute(
                "UPDATE outbox SET state = ?, next_attempt_at = NULL, last_error = ? "
                "WHERE reply_id = ?",
                (OutboxState.FAILED.value, error[:2000], reply_id),
            )
