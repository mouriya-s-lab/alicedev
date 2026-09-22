"""Command dispatch: slash commands and addressed natural-language routing.

Middleware order (ARCHITECTURE §10): chat whitelist -> command parse ->
permission -> handler. Denied calls are silently dropped (logged) to avoid
spamming the group.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from alicedev.commands.context import CommandContext, Mention, QuotedMessage
from alicedev.commands.interpret import handle_interpret
from alicedev.github.parser import parse_github_ref
from alicedev.paseo.session_actor import InjectBusyTimeout, SessionActorError

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

    from alicedev.commands.context import Services
    from alicedev.commands.registry import CommandRegistry

_LOG = logging.getLogger("alicedev.commands.dispatch")

_SLASH = re.compile(r"^\s*[/／]\s*(\S+)\s*(.*)$", re.DOTALL)
_SESSION_TOKEN = re.compile(r"s_[a-z2-7]{10}")


def _safe_session_error(exc: BaseException) -> str:
    """Keep actor diagnostics useful without exposing an internal session ref."""
    detail = _SESSION_TOKEN.sub("该会话", str(exc)).strip()
    return detail or "内部错误，请稍后再试。"


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

    async def _safe_reply(self, ctx: CommandContext, text: str) -> None:
        """Reply to the user; never let a failed reply mask the original error."""
        try:
            await ctx.reply_text(text)
        except Exception:  # noqa: BLE001
            _LOG.exception("failed to send error reply")

    async def handle_message(self, event: "AstrMessageEvent") -> bool:
        """Dispatch slash commands, then addressed natural-language messages."""
        config = self._services.config
        if not config.chat_allowed(event.unified_msg_origin):
            _LOG.info("chat not allowed: %s", event.unified_msg_origin)
            return False

        resolved = self._resolve_command(event)
        if resolved is not None:
            spec, args = resolved
            ctx = build_context(event, self._services, args)
            if spec.admin_only and not ctx.is_admin:
                _LOG.info("permission denied: %s /%s", ctx.user_key, spec.name)
                event.stop_event()
                return False
            try:
                await spec.handler(ctx)
            except SessionActorError as exc:
                _LOG.exception("command handler failed: /%s", spec.name)
                await self._safe_reply(ctx, f"处理失败：{_safe_session_error(exc)}")
            except Exception:  # noqa: BLE001 - a bad handler must not crash the adapter
                _LOG.exception("command handler failed: /%s", spec.name)
                await self._safe_reply(ctx, "处理失败：内部错误，请稍后再试")
            event.stop_event()
            return True

        # An addressed/private unknown slash command is handled explicitly so
        # it cannot fall through into current-session natural injection.
        if self._has_slash_prefix(event):
            if not self._is_addressed(event):
                return False
            token = self._slash_token(event)
            label = f"：/{token}" if token else ""
            ctx = build_context(event, self._services, "")
            await self._safe_reply(ctx, f"未知命令{label}。发送 /alicedev 查看帮助。")
            event.stop_event()
            return True
        return await self.handle_natural_message(event)

    def _resolve_command(self, event: "AstrMessageEvent"):
        """Return ``(CommandSpec, args)`` if the message is a known command."""
        raw = ""
        try:
            raw = event.message_obj.message_str or ""
        except Exception:  # noqa: BLE001
            raw = ""
        stripped = (event.message_str or "").strip()

        # 1) raw text still carrying the slash prefix.
        parsed = parse_command(raw) or parse_command(stripped)
        if parsed is not None:
            token, args = parsed
            spec = self._registry.resolve(token)
            if spec is not None:
                return spec, args

        # 2) prefix stripped by the waking stage: try the first bare token.
        woke = bool(getattr(event, "is_at_or_wake_command", False)) or raw.strip()[:1] in (
            "/",
            "／",
        )
        if woke and stripped:
            token, _, rest = stripped.partition(" ")
            spec = self._registry.resolve(token.strip())
            if spec is not None:
                return spec, rest.strip()
        return None

    @staticmethod
    def _slash_token(event: "AstrMessageEvent") -> str | None:
        raw = ""
        try:
            raw = event.message_obj.message_str or ""
        except Exception:  # noqa: BLE001
            pass
        for source in (raw, event.message_str or ""):
            parsed = parse_command(source)
            if parsed is not None:
                return parsed[0]
        return None

    @staticmethod
    def _has_slash_prefix(event: "AstrMessageEvent") -> bool:
        raw = ""
        try:
            raw = event.message_obj.message_str or ""
        except Exception:  # noqa: BLE001
            pass
        return any(
            (source or "").lstrip().startswith(("/", "／"))
            for source in (raw, event.message_str or "")
        )

    @staticmethod
    def _is_private_chat(event: "AstrMessageEvent") -> bool:
        checker = getattr(event, "is_private_chat", None)
        try:
            return bool(checker() if callable(checker) else checker)
        except Exception:  # noqa: BLE001
            _LOG.debug("private-chat detection failed", exc_info=True)
            return False

    @classmethod
    def _is_addressed(cls, event: "AstrMessageEvent") -> bool:
        return bool(getattr(event, "is_at_or_wake_command", False)) or cls._is_private_chat(
            event
        )

    @staticmethod
    def _is_exact_github_url(text: str) -> bool:
        value = text.strip()
        if not value or any(char.isspace() for char in value):
            return False
        try:
            parsed = urlsplit(value)
        except ValueError:
            return False
        if not parsed.scheme or not parsed.netloc:
            return False
        try:
            ref = parse_github_ref(value)
        except Exception:  # noqa: BLE001
            return False
        # ``#n`` parses as a GithubRef with no kind; natural routing accepts
        # only a full URL, while /解读 retains the shorthand explicitly.
        # Query strings and fragments remain part of an otherwise exact URL.
        return ref is not None and getattr(ref, "kind", None) is not None

    async def handle_natural_message(self, event: "AstrMessageEvent") -> bool:
        """Handle one addressed/private natural message, if applicable."""
        if not self._is_addressed(event):
            return False
        text = (event.message_str or "").strip()
        if not text or self._has_slash_prefix(event):
            return False

        ctx = build_context(event, self._services, text)
        if self._is_exact_github_url(text):
            try:
                await handle_interpret(ctx)
            except SessionActorError as exc:
                _LOG.exception("natural GitHub interpretation failed")
                await self._safe_reply(ctx, f"处理失败：{_safe_session_error(exc)}")
            except Exception:  # noqa: BLE001
                _LOG.exception("natural GitHub interpretation failed")
                await self._safe_reply(ctx, "处理失败：无法读取链接或创建会话，请稍后再试")
            event.stop_event()
            return True

        try:
            current = await self._services.sessions.current_for_chat(ctx.chat_key)
        except SessionActorError as exc:
            _LOG.exception("current session lookup failed")
            await self._safe_reply(ctx, f"处理失败：{_safe_session_error(exc)}")
            event.stop_event()
            return True
        except Exception:  # noqa: BLE001
            _LOG.exception("current session lookup failed")
            await self._safe_reply(ctx, "处理失败：无法读取当前会话，请稍后再试")
            event.stop_event()
            return True

        if current is None:
            await self._safe_reply(
                ctx, "当前没有会话，请先使用 /需求 或 /帮我调查 创建会话。"
            )
            event.stop_event()
            return True

        try:
            await self._services.sessions.inject(
                session_ref=current.session_ref,
                text=text,
                platform_message_id=event.message_obj.message_id,
                sender_key=ctx.user_key,
            )
        except InjectBusyTimeout:
            await self._safe_reply(ctx, "AI 仍在处理，稍后再试。")
        except SessionActorError as exc:
            _LOG.exception("natural current-session injection failed")
            await self._safe_reply(ctx, f"处理失败：{_safe_session_error(exc)}")
        except Exception:  # noqa: BLE001
            _LOG.exception("natural current-session injection failed")
            await self._safe_reply(ctx, "处理失败：无法继续当前会话，请稍后再试")
        event.stop_event()
        return True
