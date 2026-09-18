"""Internal HTTP API server (ARCHITECTURE §6).

Runs inside the plugin process on the docker network (``X-Alicedev-Token``).
Owns reply delivery (at-most-once + idempotent replay), status, and health, plus
the ``initialize``/``terminate`` draining lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol

from aiohttp import web

from alicedev.api.payloads import InvalidPayload, parse_reply_payload, payload_digest
from alicedev.api.reply import ReplyRenderer, ReplyValidationError
from alicedev.reports import ReportError
from alicedev.store.replies_repo import ClaimOutcome, RepliesRepo

if TYPE_CHECKING:
    from alicedev.commands.registry import CommandRegistry
    from alicedev.config import PluginConfig
    from alicedev.store.db import Store
    from alicedev.store.sessions_repo import SessionsRepo
    from alicedev.templates.registry import TemplateRegistry

_LOG = logging.getLogger("alicedev.api.server")

_DRAIN_TIMEOUT_S = 10.0


class PlatformSender(Protocol):
    async def send(self, chat_key: str, components: list[Any]) -> list[str]:
        """Send a proactive message chain to a chat; return platform message ids."""
        ...


class InternalApi:
    def __init__(
        self,
        *,
        config: "PluginConfig",
        store: "Store",
        sessions: "SessionsRepo",
        templates: "TemplateRegistry",
        commands: "CommandRegistry",
        renderer: ReplyRenderer,
        sender: PlatformSender,
        platforms_fn: Callable[[], list[str]],
    ) -> None:
        self._config = config
        self._store = store
        self._sessions = sessions
        self._templates = templates
        self._commands = commands
        self._replies = RepliesRepo(store)
        self._renderer = renderer
        self._sender = sender
        self._platforms_fn = platforms_fn

        self._app = web.Application(middlewares=[self._auth_and_drain])
        self._app.add_routes(
            [
                web.post("/v1/reply", self._handle_reply),
                web.get("/v1/sessions/{session}", self._handle_session),
                web.get("/v1/status", self._handle_status),
                web.get("/v1/health", self._handle_health),
            ]
        )
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._generation = 0
        self._started_at = time.monotonic()
        self._draining = False
        self._inflight = 0

    @property
    def generation(self) -> int:
        return self._generation

    async def start(self) -> None:
        self._draining = False
        self._generation += 1
        self._started_at = time.monotonic()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner,
            host=self._config.internal_api_host,
            port=self._config.internal_api_port,
            reuse_port=True,
        )
        await self._site.start()
        _LOG.info(
            "internal API listening on %s:%s (generation=%d)",
            self._config.internal_api_host, self._config.internal_api_port, self._generation,
        )

    async def stop(self) -> None:
        self._draining = True
        deadline = time.monotonic() + _DRAIN_TIMEOUT_S
        while self._inflight > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    # --- middleware -----------------------------------------------------

    @web.middleware
    async def _auth_and_drain(self, request: web.Request, handler) -> web.StreamResponse:
        if self._draining:
            return web.json_response({"error": "draining"}, status=503)
        token = request.headers.get("X-Alicedev-Token", "")
        if not self._config.internal_token or token != self._config.internal_token:
            return web.json_response({"error": "unauthorized"}, status=401)
        self._inflight += 1
        try:
            return await handler(request)
        finally:
            self._inflight -= 1

    # --- handlers -------------------------------------------------------

    async def _handle_health(self, request: web.Request) -> web.StreamResponse:
        return web.json_response({"generation": self._generation})

    async def _handle_status(self, request: web.Request) -> web.StreamResponse:
        counts = await self._sessions.counts()
        return web.json_response(
            {
                "ok": not self._draining,
                "generation": self._generation,
                "uptime_s": int(time.monotonic() - self._started_at),
                "platforms": self._platforms_fn(),
                "commands": [c.name for c in self._commands.all()],
                "templates": [t.name for t in self._templates.all()],
                "sessions": {
                    "active": counts.get("active", 0),
                    "closed": counts.get("closed", 0),
                },
            }
        )

    async def _handle_session(self, request: web.Request) -> web.StreamResponse:
        session_ref = request.match_info["session"]
        record = await self._sessions.get(session_ref)
        if record is None:
            return web.json_response({"error": "session_unknown"}, status=404)
        tpl = self._templates.by_name(record.template)
        reply_spec = _reply_spec_dict(tpl.reply) if tpl else None
        return web.json_response(
            {
                "session": record.session_ref,
                "chat_key": record.chat_key,
                "template": record.template,
                "status": record.status.value,
                "reply_spec": reply_spec,
            }
        )

    async def _handle_reply(self, request: web.Request) -> web.StreamResponse:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_payload"}, status=400)

        session_ref = body.get("session")
        reply_id = body.get("reply_id")
        msgs = body.get("msgs")
        raw_reply = body.get("reply")
        if not isinstance(session_ref, str) or not isinstance(reply_id, str):
            return web.json_response({"error": "invalid_payload"}, status=400)
        if not isinstance(msgs, list) or not all(isinstance(m, str) for m in msgs):
            return web.json_response({"error": "invalid_payload"}, status=400)

        record = await self._sessions.get(session_ref)
        if record is None:
            return web.json_response({"error": "session_unknown"}, status=404)

        tpl = self._templates.by_name(record.template)
        if tpl is None:
            return web.json_response({"error": "template_unknown"}, status=400)

        try:
            payload = parse_reply_payload(raw_reply)
            self._renderer.validate(payload, tpl.reply)
        except InvalidPayload:
            return web.json_response({"error": "invalid_payload"}, status=400)
        except ReplyValidationError as exc:
            return web.json_response({"error": exc.error}, status=400)

        digest = payload_digest(raw_reply)

        # Claim under the store lock so the check-then-write is atomic.
        async with self._store.lock:
            claim = await self._replies.claim(
                reply_id=reply_id, session_ref=session_ref, msgs=list(msgs),
                payload_sha256=digest,
            )
        if claim.outcome is ClaimOutcome.CONFLICT:
            return web.json_response({"error": "reply_id_conflict"}, status=409)
        if claim.outcome is ClaimOutcome.REPLAY:
            return web.json_response(
                {"status": "replayed", "platform_message_ids": claim.platform_message_ids}
            )

        # Fresh claim: render, send, record.
        try:
            rendered = await self._renderer.render(payload, tpl.reply)
        except ReplyValidationError as exc:
            await self._replies.mark_failed(reply_id)
            return web.json_response({"error": exc.error}, status=400)
        except ReportError as exc:
            await self._replies.mark_failed(reply_id)
            return web.json_response({"error": exc.error}, status=400)

        try:
            ids = await self._sender.send(record.chat_key, rendered.components)
        except Exception as exc:  # noqa: BLE001 - map any adapter failure to 502
            _LOG.exception("platform send failed for %s", session_ref)
            await self._replies.mark_failed(reply_id)
            return web.json_response(
                {"error": "platform_send_failed", "detail": str(exc)}, status=502
            )

        async with self._store.lock:
            await self._replies.mark_sent(reply_id, ids)
            for pmid in ids:
                await self._store.execute(
                    "INSERT INTO outbound (platform_message_id, chat_key, session_ref) "
                    "VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                    (pmid, record.chat_key, session_ref),
                )
        await self._sessions.touch(session_ref)

        out: dict[str, Any] = {"status": "sent", "platform_message_ids": ids}
        if rendered.report_url:
            out["report_url"] = rendered.report_url
        return web.json_response(out)


def _reply_spec_dict(reply) -> dict[str, Any]:
    return {
        "kinds": list(reply.kinds),
        "image_templates": list(reply.image_templates),
        "text_templates": list(reply.text_templates),
        "stickers": list(reply.stickers),
        "max_text_chars": reply.max_text_chars,
    }
