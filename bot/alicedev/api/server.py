"""Internal HTTP API server (ARCHITECTURE §6).

Runs inside the plugin process on the docker network (``X-Alicedev-Token``):
``/v1/reply`` (enqueue), ``/v1/agents/{agent}``, ``/v1/sessions/{session_id}``, ``/v1/status``,
``/v1/health``,
plus the ``initialize``/``terminate`` draining lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Callable

from aiohttp import web

from alicedev.dsl.model import AgentState, Registry
from alicedev.render import views

if TYPE_CHECKING:
    from alicedev.api.intake import ReplyIntake
    from alicedev.config import PluginConfig
    from alicedev.outbox.service import Outbox
    from alicedev.scheduler.engine import Scheduler
    from alicedev.store.agents_repo import AgentsRepo
    from alicedev.store.sessions_repo import SessionsRepo

_LOG = logging.getLogger("alicedev.api.server")

_DRAIN_TIMEOUT_S = 10.0


def read_revision(config: "PluginConfig") -> str:
    """The deployed commit written by ``deployrun`` into ``bot/REVISION``."""
    try:
        return config.revision_path.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


class InternalApi:
    def __init__(
        self,
        *,
        config: "PluginConfig",
        intake: "ReplyIntake",
        sessions: "SessionsRepo",
        agents: "AgentsRepo",
        scheduler: "Scheduler",
        outbox: "Outbox",
        registry: Callable[[], Registry],
        platforms_fn: Callable[[], list[str]],
        generation: int,
    ) -> None:
        self._config = config
        self._intake = intake
        self._sessions = sessions
        self._agents = agents
        self._scheduler = scheduler
        self._outbox = outbox
        self._registry = registry
        self._platforms_fn = platforms_fn
        self._generation = generation
        self._revision = read_revision(config)

        self._app = web.Application(middlewares=[self._auth_and_drain])
        self._app.add_routes(
            [
                web.post("/v1/reply", self._handle_reply),
                web.get("/v1/agents/{agent}", self._handle_agent),
                web.get("/v1/sessions/{session_id}", self._handle_session),
                web.get("/v1/status", self._handle_status),
                web.get("/v1/health", self._handle_health),
            ]
        )
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._started_at = time.monotonic()
        self._draining = False
        self._inflight = 0

    @property
    def app(self) -> web.Application:
        return self._app

    @property
    def generation(self) -> int:
        return self._generation

    async def start(self) -> None:
        self._draining = False
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
            "internal API listening on %s:%s (generation=%d revision=%s)",
            self._config.internal_api_host, self._config.internal_api_port,
            self._generation, self._revision,
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

    @web.middleware
    async def _auth_and_drain(self, request: web.Request, handler) -> web.StreamResponse:
        if self._draining:
            return web.json_response({"error": "draining"}, status=503)
        if request.path != "/v1/health":  # liveness probe carries no token
            token = request.headers.get("X-Alicedev-Token", "")
            if not self._config.internal_token or token != self._config.internal_token:
                return web.json_response({"error": "unauthorized"}, status=401)
        self._inflight += 1
        try:
            return await handler(request)
        finally:
            self._inflight -= 1

    async def _handle_health(self, request: web.Request) -> web.StreamResponse:
        return web.json_response({"generation": self._generation, "revision": self._revision})

    async def _handle_status(self, request: web.Request) -> web.StreamResponse:
        registry = self._registry()
        sessions = await self._sessions.counts(self._scheduler.ended_states())
        agents = await self._agents.counts()
        return web.json_response(
            {
                "ok": not self._draining,
                "generation": self._generation,
                "revision": self._revision,
                "uptime_s": int(time.monotonic() - self._started_at),
                "platforms": self._platforms_fn(),
                "commands": [c.name for c in registry.command_list()],
                "scenarios": sorted(registry.scenarios),
                "dsl_errors": [asdict(e) for e in registry.errors],
                "sessions": sessions,
                "agents": {"active": agents.get("active", 0), "closed": agents.get("closed", 0)},
                "outbox_pending": await self._outbox.repo.pending_count(),
            }
        )

    async def _handle_agent(self, request: web.Request) -> web.StreamResponse:
        agent = await self._agents.get(request.match_info["agent"])
        if agent is None:
            return web.json_response({"error": "agent_unknown"}, status=404)
        row = await self._sessions.get(agent.session_id)
        if row is None:
            return web.json_response({"error": "agent_unknown"}, status=404)
        reply_spec: dict[str, Any] | None = None
        scenario = self._scheduler.scenario(row.scenario)
        if scenario is not None:
            try:
                state = scenario.state(agent.state)
            except KeyError:
                state = None
            if isinstance(state, AgentState) and state.reply is not None:
                reply_spec = asdict(state.reply)
        return web.json_response(
            {
                "agent": agent.agent_ref,
                "session_no": row.no,
                "chat_key": row.chat_key,
                "state": agent.state,
                "status": agent.status.value,
                "reply_spec": reply_spec,
            }
        )

    async def _handle_session(self, request: web.Request) -> web.StreamResponse:
        raw = request.match_info["session_id"]
        row = await self._sessions.get(int(raw)) if raw.isdecimal() else None
        if row is None:
            return web.json_response({"error": "session_unknown"}, status=404)
        view = await self._sessions.view(row)
        return web.json_response(views.session_detail(view, self._scheduler.scenario(row.scenario)))

    async def _handle_reply(self, request: web.Request) -> web.StreamResponse:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "invalid_payload"}, status=400)
        result = await self._intake.handle(body)
        return web.json_response(dict(result.body), status=result.status)
