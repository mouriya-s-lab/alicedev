"""alicedev AstrBot plugin entry point.

AstrBot imports this module as ``data.plugins.alicedev.main``. The plugin
directory is put on ``sys.path`` so the bundled ``alicedev`` package imports
absolutely. AstrBot's hot reload only purges ``data.plugins.alicedev*`` from
``sys.modules``, so the bundled package is purged here before it is imported;
otherwise a reload would keep running the previous revision's code.
``initialize()`` wires config → store → DSL registry → paseo CLI
control → outbox → scheduler → reply intake → internal API, then starts the
outbox worker, idle sweeper and restart recovery.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)
for _name in [n for n in sys.modules if n == "alicedev" or n.startswith("alicedev.")]:
    del sys.modules[_name]

from astrbot.api.event import AstrMessageEvent, filter  # noqa: E402
from astrbot.api.star import Context, Star, register  # noqa: E402
from astrbot.core.message.message_event_result import MessageChain  # noqa: E402

from alicedev import dsl_api  # noqa: E402
from alicedev.actions.executor import Dispatcher  # noqa: E402
from alicedev.api.intake import ReplyIntake  # noqa: E402
from alicedev.api.server import InternalApi  # noqa: E402
from alicedev.config import PluginConfig  # noqa: E402
from alicedev.dsl.model import Registry  # noqa: E402
from alicedev.gateway_client import GatewayClient  # noqa: E402
from alicedev.github.client import GithubClient  # noqa: E402
from alicedev.outbox.render import OutboxRenderer  # noqa: E402
from alicedev.outbox.service import Outbox  # noqa: E402
from alicedev.paseo.agent_actor import AgentActor  # noqa: E402
from alicedev.paseo.cli import CliPaseoControl  # noqa: E402
from alicedev.paseo.sweeper import IdleSweeper  # noqa: E402
from alicedev.render.cards import CardRenderer  # noqa: E402
from alicedev.reports import ReportPublisher  # noqa: E402
from alicedev.scheduler.engine import Scheduler  # noqa: E402
from alicedev.status import BotStatus  # noqa: E402
from alicedev.store.agents_repo import AgentsRepo  # noqa: E402
from alicedev.store.db import Store  # noqa: E402
from alicedev.store.exchanges_repo import ExchangesRepo  # noqa: E402
from alicedev.store.favorites_repo import FavoritesRepo  # noqa: E402
from alicedev.store.messages_repo import MessagesRepo  # noqa: E402
from alicedev.store.sessions_repo import SessionsRepo  # noqa: E402

_LOG = logging.getLogger("alicedev")


class _ContextSender:
    """Outbox ``PlatformSender`` over AstrBot's proactive send.

    AstrBot's ``send_message`` returns no platform message ids, so none are
    recorded; quote resolution relies on the ``%n`` marker line instead.
    """

    def __init__(self, context: Context) -> None:
        self._context = context

    async def send(self, chat_key: str, components: list) -> list[str]:
        ok = await self._context.send_message(chat_key, MessageChain(chain=list(components)))
        if not ok:
            raise RuntimeError(f"no platform for session {chat_key}")
        return []


def _next_generation(data_dir: Path) -> int:
    """Monotonic plugin load counter, surfaced by ``/v1/health`` for reload checks."""
    path = data_dir / "generation"
    try:
        current = int(path.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        current = 0
    generation = current + 1
    path.write_text(str(generation), encoding="utf-8")
    return generation


@register("alicedev", "mouriya-s-lab", "QQ 群 <-> paseo 开发环境桥接", "0.2.0")
class AliceDevPlugin(Star):
    def __init__(self, context: Context, config=None) -> None:
        super().__init__(context)
        self._raw_config = config or {}
        self._store: Store | None = None
        self._registry: Registry | None = None
        self._outbox: Outbox | None = None
        self._scheduler: Scheduler | None = None
        self._sweeper: IdleSweeper | None = None
        self._api: InternalApi | None = None
        self._dispatcher: Dispatcher | None = None

    def _current_registry(self) -> Registry:
        assert self._registry is not None
        return self._registry

    async def initialize(self) -> None:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        data_dir = Path(get_astrbot_plugin_data_path()) / "alicedev"
        data_dir.mkdir(parents=True, exist_ok=True)
        config = PluginConfig.from_astrbot(
            self._raw_config, data_dir=data_dir, plugin_dir=Path(_PLUGIN_DIR)
        )

        registry = dsl_api.load(config.templates_root)
        self._registry = registry
        for error in registry.errors:
            _LOG.error("DSL %s:%s %s", error.path, error.line, error.message)

        store = Store(str(config.duckdb_path))
        await store.open()
        self._store = store
        sessions = SessionsRepo(store)
        agents = AgentsRepo(store)
        messages = MessagesRepo(store)
        favorites = FavoritesRepo(store)
        exchanges = ExchangesRepo(store)

        paseo = CliPaseoControl(
            container=config.paseo_container, paseo_bin=config.paseo_bin,
            docker_bin=config.docker_bin,
        )
        actor = AgentActor(store=store, agents=agents, messages=messages, paseo=paseo, config=config)
        renderer = OutboxRenderer(config=config, cards=CardRenderer(self, config.templates_root))
        outbox = Outbox(store=store, renderer=renderer, sender=_ContextSender(self.context))
        self._outbox = outbox
        gateway = GatewayClient(config.gateway_url, config.internal_token)
        scheduler = Scheduler(
            store=store, sessions=sessions, agents=agents, messages=messages, actor=actor,
            paseo=paseo, outbox=outbox, gateway=gateway, config=config,
            registry=self._current_registry,
        )
        self._scheduler = scheduler
        intake = ReplyIntake(
            store=store, sessions=sessions, agents=agents, outbox=outbox,
            reports=ReportPublisher(store=store, config=config), renderer=renderer,
            scheduler=scheduler, config=config,
        )
        self._dispatcher = Dispatcher(
            config=config, store=store, sessions=sessions, favorites=favorites,
            scheduler=scheduler, outbox=outbox,
            github=GithubClient(config.github_token, config.default_repo),
            registry=self._current_registry, agents=agents, exchanges=exchanges,
            status_fn=self._bot_status,
        )
        self._api = InternalApi(
            config=config, intake=intake, sessions=sessions, agents=agents, scheduler=scheduler,
            outbox=outbox, registry=self._current_registry, platforms_fn=self._platform_names,
            generation=_next_generation(data_dir), dispatcher=self._dispatcher,
        )
        self._sweeper = IdleSweeper(
            agents=agents, actor=actor, config=config,
            is_conversational=scheduler.is_conversational_agent,
        )

        outbox.start()
        await self._api.start()
        self._sweeper.start()
        await scheduler.recover()
        _LOG.info(
            "alicedev initialized: %d commands, %d scenarios, %d DSL errors",
            len(registry.command_list()), len(registry.scenarios), len(registry.errors),
        )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if self._dispatcher is None:
            return
        try:
            await self._dispatcher.handle_event(event)
        except Exception:  # noqa: BLE001 - never crash the adapter pipeline
            _LOG.exception("message dispatch failed")

    async def terminate(self) -> None:
        # Stop accepting replies → sweeper → scheduler tasks → outbox worker → store.
        if self._api is not None:
            await self._api.stop()
        if self._sweeper is not None:
            await self._sweeper.stop()
        if self._scheduler is not None:
            await self._scheduler.stop()
        if self._outbox is not None:
            await self._outbox.stop()
        if self._store is not None:
            await self._store.close()

    async def _bot_status(self) -> BotStatus:
        assert self._api is not None
        return await self._api.bot_status()

    def _platform_names(self) -> list[str]:
        try:
            return [p.meta().name for p in self.context.platform_manager.platform_insts]
        except Exception:  # noqa: BLE001
            return []
