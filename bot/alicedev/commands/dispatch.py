"""Command dispatch: one slash entrypoint + optional bare-link auto-routing.

Middleware order (ARCHITECTURE §10): chat whitelist -> command parse ->
permission -> handler. Denied calls are silently dropped (logged) to avoid
spamming the group.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from alicedev.commands.context import CommandContext, Mention, QuotedMessage

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

    from alicedev.commands.context import Services
    from alicedev.commands.registry import CommandRegistry

_LOG = logging.getLogger("alicedev.commands.dispatch")

_SLASH = re.compile(r"^\s*[/／]\s*(\S+)\s*(.*)$", re.DOTALL)


def parse_command(message_str: str) -> tuple[str, str] | None:
    """Return ``(token, args)`` if the message is a slash command, else None."""
    m = _SLASH.match(message_str or "")
    if not m:
        return None
    return m.group(1), m.group(2).strip()


def user_key_of(event: "AstrMessageEvent", sender_id: str) -> str:
    return f"{event.get_platform_id()}:{sender_id}"


def build_context(
    event: "AstrMessageEvent", services: "Services", args: str
) -> CommandContext:
    sender_id = event.get_sender_id()
    user_key = user_key_of(event, sender_id)
    return CommandContext(
        event=event,
        args=args,
        chat_key=event.unified_msg_origin,
        user_key=user_key,
        sender_name=event.get_sender_name(),
        is_admin=services.config.is_admin(user_key),
        services=services,
        quoted=extract_quoted(event),
        mentions=extract_mentions(event),
    )


def extract_quoted(event: "AstrMessageEvent") -> QuotedMessage | None:
    for comp in _components(event):
        if type(comp).__name__ == "Reply":
            images = tuple(
                str(getattr(c, "url", "") or getattr(c, "file", ""))
                for c in (getattr(comp, "chain", None) or [])
                if type(c).__name__ == "Image"
            )
            sender_id = str(getattr(comp, "sender_id", "") or "")
            return QuotedMessage(
                platform_message_id=str(getattr(comp, "id", "") or ""),
                sender_key=user_key_of(event, sender_id) if sender_id else "",
                sender_name=str(getattr(comp, "sender_nickname", "") or ""),
                text=str(getattr(comp, "message_str", "") or ""),
                image_urls=tuple(u for u in images if u),
            )
    return None


def extract_mentions(event: "AstrMessageEvent") -> list[Mention]:
    mentions: list[Mention] = []
    for comp in _components(event):
        if type(comp).__name__ == "At":
            qq = str(getattr(comp, "qq", "") or "")
            name = str(getattr(comp, "name", "") or "")
            mentions.append(Mention(user_key=user_key_of(event, qq), name=name))
    return mentions


def _components(event: "AstrMessageEvent") -> list:
    try:
        return list(event.get_messages() or [])
    except Exception:  # noqa: BLE001
        return []


class CommandDispatcher:
    def __init__(self, registry: "CommandRegistry", services: "Services") -> None:
        self._registry = registry
        self._services = services

    async def handle_slash(self, event: "AstrMessageEvent") -> bool:
        """Dispatch a slash command. Returns True if a command was handled."""
        config = self._services.config
        if not config.chat_allowed(event.unified_msg_origin):
            _LOG.info("chat not allowed: %s", event.unified_msg_origin)
            return False
        parsed = parse_command(event.message_str)
        if parsed is None:
            return False
        token, args = parsed
        spec = self._registry.resolve(token)
        if spec is None:
            return False
        ctx = build_context(event, self._services, args)
        if spec.admin_only and not ctx.is_admin:
            _LOG.info("permission denied: %s /%s", ctx.user_key, token)
            return False
        try:
            await spec.handler(ctx)
        except Exception:  # noqa: BLE001 - a bad handler must not crash the adapter
            _LOG.exception("command handler failed: /%s", token)
        return True

    async def handle_bare_link(self, event: "AstrMessageEvent") -> bool:
        """Auto-route a message that is (only) a GitHub issue/PR link.

        Uses ``services.github`` to resolve the ref and ``templates.by_link`` to
        find the matching template, then creates a session. No-op when the
        github client or a link template is unavailable.
        """
        if not self._services.config.chat_allowed(event.unified_msg_origin):
            return False
        text = (event.message_str or "").strip()
        if not text or parse_command(text) is not None:
            return False
        github = getattr(self._services, "github", None)
        if github is None:
            return False
        try:
            ref = github.try_parse(text)
        except Exception:  # noqa: BLE001
            return False
        if ref is None:
            return False
        template = self._services.templates.by_link(getattr(ref, "link_kind", ""))
        if template is None:
            return False
        try:
            item = await github.fetch(ref)
            vars = github.to_template_vars(item)
        except Exception:  # noqa: BLE001
            _LOG.exception("github link fetch failed")
            return False
        ctx = build_context(event, self._services, text)
        record = await self._services.sessions.create_and_inject(
            chat_key=ctx.chat_key, template=template, vars=vars,
            created_by=ctx.user_key, platform_message_id=event.message_obj.message_id,
            sender_key=ctx.user_key,
        )
        await ctx.reply_text(f"已创建 {record.session_ref}")
        return True
