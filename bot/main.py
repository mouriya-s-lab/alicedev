"""alicedev AstrBot plugin entry point.

AstrBot imports this module as ``data.plugins.alicedev.main``. We insert the
plugin directory into ``sys.path`` so the bundled ``alicedev`` package is
importable with absolute ``alicedev.*`` imports (the convention every slice
uses). ``initialize()`` wires config -> Store -> TemplateRegistry -> Services ->
CommandRegistry, then starts the internal API and idle sweeper.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from astrbot.api.event import AstrMessageEvent, filter  # noqa: E402
from astrbot.api.star import Context, Star, register  # noqa: E402
from astrbot.core.message.message_event_result import MessageChain  # noqa: E402

from alicedev.api.reply import ReplyRenderer  # noqa: E402
from alicedev.api.server import InternalApi  # noqa: E402
from alicedev.commands import (  # noqa: E402
    archive,
    continuation,
    favorites,
    help,
    interpret,
    links,
    lists,
    requirement,
    upgrade,
)
from alicedev.commands.context import Services  # noqa: E402
from alicedev.commands.dispatch import CommandDispatcher  # noqa: E402
from alicedev.commands.registry import CommandRegistry  # noqa: E402
from alicedev.config import PluginConfig  # noqa: E402
from alicedev.paseo.daemon_ws import WSDaemonControl  # noqa: E402
from alicedev.paseo.mcp import MCPPaseoControl  # noqa: E402
from alicedev.paseo.session_actor import SessionActor  # noqa: E402
from alicedev.paseo.sweeper import IdleSweeper  # noqa: E402
from alicedev.reports import ReportPublisher  # noqa: E402
from alicedev.store.db import Store  # noqa: E402
from alicedev.store.messages_repo import MessagesRepo  # noqa: E402
from alicedev.store.sessions_repo import SessionsRepo  # noqa: E402
from alicedev.store.upgrade_runs_repo import UpgradeRunsRepo  # noqa: E402
from alicedev.templates.registry import TemplateRegistry  # noqa: E402
from alicedev.upgrade import (  # noqa: E402
    CandidateReadyPayload,
    DeployResultPayload,
    E2ENotConvergingPayload,
    MainSyncFailedPayload,
    UpgradeConductor as UpgradeDriver,
)

_LOG = logging.getLogger("alicedev")

def _optional_card_renderer(star: "AliceDevPlugin", templates_root: Path):
    """Return a CardRenderer if CardsFavorites has landed it, else None."""
    try:
        from alicedev.render.cards import CardRenderer  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        return CardRenderer(star, templates_root)  # signature owned by CardsFavorites
    except Exception:  # noqa: BLE001
        _LOG.warning("CardRenderer present but could not be constructed", exc_info=True)
        return None

def _optional_gateway_client(config: PluginConfig):
    try:
        from alicedev.gateway_client import GatewayClient  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        return GatewayClient(config.gateway_url, config.internal_token)
    except Exception:  # noqa: BLE001
        return None


def _optional_github_client(config: PluginConfig):
    try:
        from alicedev.github.client import GithubClient  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        return GithubClient(config.github_token, config.default_repo)
    except Exception:  # noqa: BLE001
        return None


class _ContextSender:
    """PlatformSender that proactively sends a chain to a chat_key."""

    def __init__(self, context: Context) -> None:
        self._context = context

    async def send(self, chat_key: str, components: list) -> list[str]:
        chain = MessageChain(chain=list(components))
        ok = await self._context.send_message(chat_key, chain)
        if not ok:
            raise RuntimeError(f"no platform for session {chat_key}")
        # AstrBot's proactive send returns no platform message id; synthesize one
        # so at-most-once bookkeeping and outbound tracking have a stable handle.
        return [f"alicedev-{uuid.uuid4().hex}"]


class _UpgradeEventSink:
    """Deliver the conductor's four typed boundary events to the chat layer."""

    def __init__(
        self,
        *,
        sender: _ContextSender,
        runs: UpgradeRunsRepo,
        renderer: ReplyRenderer,
    ) -> None:
        self._sender = sender
        self._runs = runs
        self._renderer = renderer

    async def _chat(self, run_id: str) -> str:
        record = await self._runs.get(run_id)
        if record is None:
            raise RuntimeError(f"upgrade run disappeared before event delivery: {run_id}")
        return record.chat_key

    async def candidate_ready(self, run_id: str, payload: CandidateReadyPayload) -> None:
        from astrbot.api.message_components import Plain

        components: list = [
            Plain(
                f"候选已就绪：{run_id}\n"
                f"candidate commit：{payload.commit}\n"
                f"批准：/升级bot approve {run_id} {payload.commit}\n"
                f"拒绝：/升级bot reject {run_id}"
            )
        ]
        for image in (payload.explain_img, payload.evidence_img):
            if image:
                components.append(self._renderer.render_inline_image(image))
        await self._sender.send(await self._chat(run_id), components)

    async def main_sync_failed(self, run_id: str, payload: MainSyncFailedPayload) -> None:
        from astrbot.api.message_components import Plain

        text = f"升级运行 {run_id}：fixed-main 对齐失败。\n{payload.reason}"
        if payload.paseo_link:
            text += f"\nPaseo：{payload.paseo_link}"
        await self._sender.send(await self._chat(run_id), [Plain(text)])

    async def e2e_not_converging(self, run_id: str, payload: E2ENotConvergingPayload) -> None:
        from astrbot.api.message_components import Plain

        summary = json.dumps(
            [round_summary.as_dict() for round_summary in payload.round_summaries],
            ensure_ascii=False,
        )
        text = f"升级运行 {run_id}：E2E 连续 5 轮未收敛。\n{summary}"
        if payload.paseo_link:
            text += f"\nPaseo：{payload.paseo_link}"
        await self._sender.send(await self._chat(run_id), [Plain(text)])

    async def deploy_result(self, run_id: str, payload: DeployResultPayload) -> None:
        from astrbot.api.message_components import Plain

        labels = {
            "active": "已上线并核实运行",
            "rolled_back": "核实失败，已自动回滚",
            "rollback_failed": "核实失败且自动回滚失败，需要人工介入",
        }
        text = f"升级运行 {run_id}：{labels.get(payload.outcome, payload.outcome)}。"
        if payload.paseo_link:
            text += f"\nPaseo：{payload.paseo_link}"
        await self._sender.send(await self._chat(run_id), [Plain(text)])


@register("alicedev", "mouriya-s-lab", "QQ 群 <-> paseo 开发环境桥接", "0.1.0")
class AliceDevPlugin(Star):
    def __init__(self, context: Context, config=None) -> None:
        super().__init__(context)
        self._raw_config = config or {}
        self._config: PluginConfig | None = None
        self._store: Store | None = None
        self._paseo: MCPPaseoControl | None = None
        self._upgrade_daemon: WSDaemonControl | None = None
        self._sessions_actor: SessionActor | None = None
        self._sweeper: IdleSweeper | None = None
        self._api: InternalApi | None = None
        self._dispatcher: CommandDispatcher | None = None
        self._services: Services | None = None

    async def initialize(self) -> None:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        data_dir = Path(get_astrbot_plugin_data_path()) / "alicedev"
        data_dir.mkdir(parents=True, exist_ok=True)
        config = PluginConfig.from_astrbot(self._raw_config, data_dir=data_dir)
        self._config = config

        store = Store(str(config.duckdb_path))
        await store.open()
        self._store = store

        sessions_repo = SessionsRepo(store)
        messages_repo = MessagesRepo(store)

        templates = TemplateRegistry()
        templates.load(config.templates_root / "prompts")

        paseo = MCPPaseoControl(config.paseo_url, config.paseo_password)
        self._paseo = paseo

        actor = SessionActor(
            store=store, sessions=sessions_repo, messages=messages_repo,
            templates=templates, paseo=paseo, config=config,
        )
        self._sessions_actor = actor

        render = _optional_card_renderer(self, config.templates_root)
        gateway = _optional_gateway_client(config)
        github = _optional_github_client(config)
        reports = ReportPublisher(store=store, config=config)
        renderer = ReplyRenderer(
            config=config, templates=templates, render=render, reports=reports
        )
        sender = _ContextSender(self.context)
        upgrade_runs = UpgradeRunsRepo(store)
        upgrade_daemon = WSDaemonControl(
            base_url=config.paseo_url,
            password=config.paseo_password,
        )
        self._upgrade_daemon = upgrade_daemon
        conductor = UpgradeDriver(
            daemon=upgrade_daemon,
            repo=upgrade_runs,
            event_sink=_UpgradeEventSink(
                sender=sender,
                runs=upgrade_runs,
                renderer=renderer,
            ),
            fixed_main=config.paseo_cwd,
            deploy_target="/srv/alicedev/bot",
            provider=config.paseo_provider,
            paseo_link=lambda workspace_id: (
                (
                    f"{config.public_base_url.rstrip('/')}/workspace/{workspace_id}"
                    if workspace_id
                    else f"{config.public_base_url.rstrip('/')}/workspace/alicedev"
                )
                if config.public_base_url
                else ""
            ),
        )

        registry = CommandRegistry()

        api = InternalApi(
            config=config, store=store, sessions=sessions_repo, templates=templates,
            commands=registry, renderer=renderer, sender=sender,
            platforms_fn=self._platform_names,
        )
        services = Services(
            store=store, templates=templates, paseo=paseo, sessions=actor,
            render=render, gateway=gateway, github=github, config=config,
            internal_api=api, conductor=conductor,
        )
        self._services = services

        # Built-in command registration (each slice's register()).
        requirement.register(registry, services)
        continuation.register(registry, services)
        favorites.register(registry, services)
        lists.register(registry, services)
        links.register(registry, services)
        interpret.register(registry, services)
        archive.register(registry, services)
        help.register(registry, services)
        upgrade.register(registry, services)

        self._dispatcher = CommandDispatcher(registry, services)

        await actor.start()
        await api.start()
        self._sweeper = IdleSweeper(sessions=sessions_repo, actor=actor, config=config)
        self._sweeper.start()
        _LOG.info(
            "alicedev initialized: %d commands, %d templates",
            len(registry.all()), len(templates.all()),
        )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if self._dispatcher is None:
            return
        try:
            await self._dispatcher.handle_message(event)
        except Exception:  # noqa: BLE001 - never crash the adapter pipeline
            _LOG.exception("message dispatch failed")

    async def terminate(self) -> None:
        # Draining order: stop accepting -> sweeper -> paseo transport -> store.
        if self._api is not None:
            await self._api.stop()
        if self._sweeper is not None:
            await self._sweeper.stop()
        if self._upgrade_daemon is not None:
            await self._upgrade_daemon.aclose()
        if self._paseo is not None:
            await self._paseo.aclose()
        if self._store is not None:
            await self._store.close()

    def _platform_names(self) -> list[str]:
        try:
            return [p.meta().name for p in self.context.platform_manager.platform_insts]
        except Exception:  # noqa: BLE001
            return []
