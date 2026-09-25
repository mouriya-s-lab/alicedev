"""Runtime test harness: a real Store/scheduler/intake/outbox over fake paseo + platform."""

from __future__ import annotations

import asyncio
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "bot") not in sys.path:
    sys.path.insert(0, str(ROOT / "bot"))

from alicedev import dsl_api  # noqa: E402
from alicedev.actions.executor import Dispatcher  # noqa: E402
from alicedev.actions.inbound import Inbound, Mention, QuotedMessage  # noqa: E402
from alicedev.api.intake import ReplyIntake  # noqa: E402
from alicedev.config import PluginConfig  # noqa: E402
from alicedev.gateway_client import IssuedToken  # noqa: E402
from alicedev.outbox.render import OutboxRenderer  # noqa: E402
from alicedev.outbox.service import Outbox  # noqa: E402
from alicedev.paseo.agent_actor import AgentActor  # noqa: E402
from alicedev.paseo.control import (  # noqa: E402
    AgentHandle,
    AgentStatus,
    GitResult,
    PaseoControl,
    PaseoError,
    WorkspaceRef,
)
from alicedev.reports import ReportPublisher  # noqa: E402
from alicedev.scheduler.engine import Scheduler  # noqa: E402
from alicedev.status import BotStatus, collect_status  # noqa: E402
from alicedev.store.agents_repo import AgentsRepo  # noqa: E402
from alicedev.store.db import Store  # noqa: E402
from alicedev.store.exchanges_repo import ExchangesRepo  # noqa: E402
from alicedev.store.favorites_repo import FavoritesRepo  # noqa: E402
from alicedev.store.messages_repo import MessagesRepo  # noqa: E402
from alicedev.store.sessions_repo import SessionsRepo  # noqa: E402

TEMPLATES = ROOT / "templates"
CHAT = "telegram:GroupMessage:-100"
ADMIN = "telegram:1"
USER = "telegram:2"


def install_fake_astrbot() -> None:
    """Minimal ``astrbot.api.message_components`` so OutboxRenderer can build chains."""
    if "astrbot.api.message_components" in sys.modules:
        return

    @dataclass
    class Plain:
        text: str

    @dataclass
    class At:
        qq: str
        name: str = ""

    @dataclass
    class Image:
        file: str

        @classmethod
        def fromFileSystem(cls, path: str) -> "Image":
            return cls(f"file://{path}")

        @classmethod
        def fromBase64(cls, data: str) -> "Image":
            return cls(f"base64://{data[:16]}")

    @dataclass
    class File:
        name: str
        file: str

    comps = types.ModuleType("astrbot.api.message_components")
    comps.Plain, comps.At, comps.Image, comps.File = Plain, At, Image, File
    sys.modules.setdefault("astrbot", types.ModuleType("astrbot"))
    sys.modules.setdefault("astrbot.api", types.ModuleType("astrbot.api"))
    sys.modules["astrbot.api.message_components"] = comps


class FakePaseo(PaseoControl):
    """In-memory paseo daemon with a scriptable git for mainsync."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.sent: list[tuple[str, str]] = []
        self.archived: list[str] = []
        self.closed: list[str] = []
        self.workspaces: list[tuple[str, str]] = []
        self.worktrees: list[dict[str, str]] = []
        self.archived_workspaces: list[str] = []
        self.statuses: dict[str, AgentStatus] = {}
        self.fail_create = False
        self.git_overrides: dict[tuple[str, ...], GitResult] = {}
        self._n = 0

    def _next(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    async def create(self, *, agent_ref, provider, cwd, title, initial_prompt, workspace_id):
        if self.fail_create:
            raise PaseoError("provider unavailable")
        agent_id = self._next("ag")
        self.created.append(dict(agent_ref=agent_ref, provider=provider, cwd=cwd, title=title,
                                 prompt=initial_prompt, workspace_id=workspace_id, agent_id=agent_id))
        self.statuses[agent_id] = AgentStatus.IDLE
        return AgentHandle(agent_id=agent_id, workspace_id=workspace_id, server_id="srv1")

    async def find_by_label(self, agent_ref):
        for c in self.created:
            if c["agent_ref"] == agent_ref:
                return AgentHandle(c["agent_id"], c["workspace_id"], "srv1")
        return None

    async def send(self, agent_id, text):
        self.sent.append((agent_id, text))

    async def status(self, agent_id):
        return self.statuses.get(agent_id, AgentStatus.IDLE)

    async def close(self, agent_id):
        self.closed.append(agent_id)

    async def archive(self, agent_id):
        self.archived.append(agent_id)

    async def workspace_local(self, path, title):
        ws = self._next("wsl")
        self.workspaces.append((ws, path))
        return WorkspaceRef(ws, path)

    async def worktree_create(self, *, repo, base_ref, slug):
        ws = self._next("wst")
        cwd = f"/root/.paseo/worktrees/{slug}"
        self.worktrees.append(dict(ws=ws, repo=repo, base=base_ref, slug=slug, cwd=cwd))
        return WorkspaceRef(ws, cwd)

    async def workspace_archive(self, workspace_id):
        self.archived_workspaces.append(workspace_id)

    async def server_id(self):
        return "srv1"

    async def git(self, repo, *args):
        if args in self.git_overrides:
            return self.git_overrides[args]
        return GitResult(0, _GIT_OK.get(args, ""), "")


_GIT_OK: dict[tuple[str, ...], str] = {
    ("rev-parse", "--is-inside-work-tree"): "true",
    ("rev-parse", "--is-bare-repository"): "false",
    ("symbolic-ref", "--quiet", "--short", "HEAD"): "main",
    ("remote", "get-url", "origin"): "https://github.com/x/alicedev.git",
    ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"): "origin/main",
    ("status", "--porcelain=v1", "--untracked-files=all"): "",
    ("merge-base", "HEAD", "origin/main"): "abc123",
    ("rev-parse", "HEAD"): "abc123",
}
# MERGE_HEAD / REBASE_HEAD must *not* exist.
GIT_NO_MARKERS = {
    ("rev-parse", "--verify", "--quiet", "MERGE_HEAD"): GitResult(1, "", ""),
    ("rev-parse", "--verify", "--quiet", "REBASE_HEAD"): GitResult(1, "", ""),
}


class FakeGateway:
    def __init__(self) -> None:
        self.issued: list[dict[str, Any]] = []

    async def issue_token(self, target: str, user_key: str, session_id: int, ttl_s: int = 21600) -> IssuedToken:
        self.issued.append(dict(target=target, user_key=user_key, session_id=session_id))
        n = len(self.issued)
        return IssuedToken(token=f"t{n}", url=f"https://dev.example/t/t{n}")


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, list[Any]]] = []
        self.fail_next = 0

    async def send(self, chat_key: str, components: list[Any]) -> list[str]:
        if self.fail_next:
            self.fail_next -= 1
            raise RuntimeError("adapter down")
        self.sent.append((chat_key, components))
        return []


class FakeCards:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.rendered: list[tuple[str, dict[str, Any]]] = []

    async def render(self, card: str, fields: dict[str, Any]) -> Path:
        self.rendered.append((card, fields))
        path = self.root / f"card{len(self.rendered)}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return path


@dataclass
class Env:
    tmp: Path
    config: PluginConfig
    store: Store
    sessions: SessionsRepo
    agents: AgentsRepo
    messages: MessagesRepo
    paseo: FakePaseo
    gateway: FakeGateway
    sender: FakeSender
    cards: FakeCards
    outbox: Outbox
    scheduler: Scheduler
    intake: ReplyIntake
    dispatcher: Dispatcher
    registry: Any
    _texts: list[str] = field(default_factory=list)

    async def drain(self) -> list[tuple[str, list[Any]]]:
        """Let background scheduler tasks settle, deliver the outbox, return new sends."""
        while self.scheduler._background:
            await asyncio.gather(*list(self.scheduler._background), return_exceptions=True)
        before = len(self.sender.sent)
        while await self.outbox.drain_once():
            pass
        return self.sender.sent[before:]

    async def texts(self) -> list[str]:
        out = []
        for _chat, comps in await self.drain():
            out.append("".join(getattr(c, "text", "") for c in comps) or
                       " ".join(type(c).__name__ for c in comps))
        return out

    async def close(self) -> None:
        await self.scheduler.stop()
        await self.store.close()


async def make_env(tmp: Path, *, templates: Path = TEMPLATES) -> Env:
    install_fake_astrbot()
    (tmp / "reports").mkdir(exist_ok=True)
    config = PluginConfig(
        internal_token="tok", gateway_url="http://gw", public_base_url="https://dev.example",
        reports_root=tmp / "reports", templates_root=templates, images_root=tmp / "images",
        stickers_root=templates / "stickers", data_dir=tmp, plugin_dir=tmp,
        admin_users=(ADMIN,), inject_wait_max_seconds=0, inject_poll_seconds=0.0,
    )
    registry = dsl_api.load(templates)
    store = Store(str(tmp / "alicedev.duckdb"))
    await store.open()
    sessions, agents, messages = SessionsRepo(store), AgentsRepo(store), MessagesRepo(store)
    paseo = FakePaseo()
    paseo.git_overrides.update(GIT_NO_MARKERS)
    gateway = FakeGateway()
    sender = FakeSender()
    cards = FakeCards(tmp)
    renderer = OutboxRenderer(config=config, cards=cards)  # type: ignore[arg-type]
    outbox = Outbox(store=store, renderer=renderer, sender=sender)
    actor = AgentActor(store=store, agents=agents, messages=messages, paseo=paseo, config=config)
    scheduler = Scheduler(
        store=store, sessions=sessions, agents=agents, messages=messages, actor=actor, paseo=paseo,
        outbox=outbox, gateway=gateway, config=config, registry=lambda: registry,  # type: ignore[arg-type]
    )
    intake = ReplyIntake(
        store=store, sessions=sessions, agents=agents, outbox=outbox,
        reports=ReportPublisher(store=store, config=config), renderer=renderer,
        scheduler=scheduler, config=config,
    )

    async def status_fn() -> BotStatus:
        return await collect_status(
            registry=registry, sessions=sessions, agents=agents, outbox=outbox,
            platforms_fn=lambda: ["telegram"], generation=1, revision="test", started_at=0.0,
            ended_states=scheduler.ended_states(),
        )

    dispatcher = Dispatcher(
        config=config, store=store, sessions=sessions, favorites=FavoritesRepo(store),
        scheduler=scheduler, outbox=outbox, github=None, registry=lambda: registry,
        agents=agents, exchanges=ExchangesRepo(store), status_fn=status_fn,
    )
    return Env(tmp, config, store, sessions, agents, messages, paseo, gateway, sender, cards,
               outbox, scheduler, intake, dispatcher, registry)


_MSG = 0


def inbound(
    text: str,
    *,
    user: str = USER,
    chat: str = CHAT,
    addressed: bool = False,
    quoted: QuotedMessage | None = None,
    mentions: tuple[Mention, ...] = (),
    message_id: str | None = None,
) -> Inbound:
    global _MSG
    _MSG += 1
    return Inbound(
        chat_key=chat, platform_id="telegram", user_key=user, sender_id=user.split(":")[1],
        sender_name=f"user{user.split(':')[1]}", chat_name="dev group", text=text,
        platform_message_id=message_id or f"m{_MSG}", is_admin=user == ADMIN,
        addressed=addressed, quoted=quoted, mentions=mentions,
    )


def quote_of(text: str) -> QuotedMessage:
    return QuotedMessage(platform_message_id="", sender_key="telegram:bot", sender_name="bot", text=text)
