"""Internal HTTP API server (ARCHITECTURE §6).

Runs inside the plugin process on the docker network (``X-Alicedev-Token``):
``/v1/reply`` (enqueue), ``/v1/tools`` (discover/invoke), ``/v1/agents/{agent}``,
``/v1/sessions/{session_id}``, ``/v1/status``, ``/v1/health``,
plus the ``initialize``/``terminate`` draining lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import json
import time
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from aiohttp import web

from alicedev.agent_tools.model import ToolFailure
from alicedev.agent_tools.parser import parse_tool_request
from alicedev.agent_tools.results import ToolDiscovery
from alicedev.agent_tools.service import ToolService
from alicedev.dsl.model import AgentState, Registry
from alicedev.domain import JsonValue
from alicedev.render import views
from alicedev.status import BotStatus, collect_status, status_json

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
        tools: ToolService,
    ) -> None:
        self._config = config
        self._intake = intake
        self._tools = tools
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
                web.get("/v1/tools", self._handle_tools),
                web.post("/v1/tools/{name}", self._handle_tool),
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

    async def bot_status(self) -> "BotStatus":
        """The bot status also shown by the /状态 card (§3.2)."""
        return await collect_status(
            registry=self._registry(),
            sessions=self._sessions,
            agents=self._agents,
            outbox=self._outbox,
            platforms_fn=self._platforms_fn,
            generation=self._generation,
            revision=self._revision,
            started_at=self._started_at,
            ended_states=self._scheduler.ended_states(),
        )

    async def _handle_status(self, request: web.Request) -> web.StreamResponse:
        return web.json_response({"ok": not self._draining, **status_json(await self.bot_status())})

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

    async def _handle_tools(self, request: web.Request) -> web.StreamResponse:
        agent_ref = request.query.get("agent")
        if request.query.keys() != {"agent"} or len(request.query.getall("agent", [])) != 1 or not agent_ref or not agent_ref.strip():
            return web.json_response({"error": "invalid_payload"}, status=400)
        result = await self._tools.discover(agent_ref)
        if isinstance(result, ToolFailure):
            return web.json_response({"error": result.error.value}, status=result.status)
        return web.json_response(_discovery_json(result))

    async def _handle_tool(self, request: web.Request) -> web.StreamResponse:
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            return web.json_response({"error": "invalid_payload"}, status=400)
        parsed = parse_tool_request(request.match_info["name"], body)
        result = parsed if isinstance(parsed, ToolFailure) else await self._tools.invoke(parsed)
        if isinstance(result, ToolFailure):
            return web.json_response({"error": result.error.value}, status=result.status)
        return web.json_response(asdict(result), dumps=_tool_dumps)


def _tool_dumps(value: JsonValue) -> str:
    def encode(item: object) -> str:
        if isinstance(item, datetime):
            return item.isoformat()
        raise TypeError(f"unsupported tool JSON type {type(item).__name__}")
    return json.dumps(value, ensure_ascii=False, default=encode)


def _discovery_json(discovery: ToolDiscovery) -> dict[str, JsonValue]:
    tools: list[JsonValue] = []
    for spec in discovery.tools:
        properties: dict[str, JsonValue] = {}
        required: list[JsonValue] = []
        for parameter in spec.parameters:
            schema: dict[str, JsonValue] = {"type": parameter.kind}
            if parameter.kind == "integer":
                schema["minimum"] = 1
            elif parameter.kind == "string":
                schema["minLength"] = 1
            properties[parameter.name] = schema
            if parameter.required:
                required.append(parameter.name)
        tools.append({"name": spec.name.value, "summary": spec.summary,
                      "input_schema": {"type": "object", "properties": properties,
                                       "required": required, "additionalProperties": False}})
    return {"agent": discovery.agent, "session_no": discovery.session_no,
            "chat_key": discovery.chat_key, "tools": tools}
