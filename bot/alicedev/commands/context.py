"""Command execution context and the aggregate ``Services`` root.

``Services`` is the dependency container every command handler receives. Fields
typed with sibling-owned classes are declared here structurally; with
``from __future__ import annotations`` the annotations stay strings, so this
module never imports sibling code at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

    from alicedev.api.server import InternalApi
    from alicedev.config import PluginConfig
    from alicedev.gateway_client import GatewayClient
    from alicedev.github.client import GithubClient
    from alicedev.commands.upgrade import UpgradeConductor
    from alicedev.paseo.control import PaseoControl
    from alicedev.render.cards import CardRenderer
    from alicedev.store.db import Store
    from alicedev.templates.registry import TemplateRegistry


@dataclass(frozen=True)
class QuotedMessage:
    """A message quoted via ``Comp.Reply``."""

    platform_message_id: str
    sender_key: str
    sender_name: str
    text: str
    image_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class Mention:
    """A user mentioned via ``Comp.At``."""

    user_key: str
    name: str


@dataclass
class Services:
    """Aggregate dependency root passed to every command handler."""

    store: "Store"
    templates: "TemplateRegistry"
    paseo: "PaseoControl"
    sessions: "SessionActor"
    render: "CardRenderer"
    gateway: "GatewayClient"
    github: "GithubClient"
    config: "PluginConfig"
    internal_api: "InternalApi"
    conductor: "UpgradeConductor | None" = None

@dataclass
class CommandContext:
    """Everything a command handler needs about one inbound invocation."""

    event: "AstrMessageEvent"
    args: str
    chat_key: str
    user_key: str
    sender_name: str
    is_admin: bool
    services: "Services"
    quoted: QuotedMessage | None = None
    mentions: list[Mention] = field(default_factory=list)

    async def reply_text(self, text: str) -> None:
        """Convenience: send a plain-text result on the originating event."""
        await self.event.send(self.event.plain_result(text))
