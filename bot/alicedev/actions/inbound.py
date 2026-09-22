"""One inbound platform message, parsed at the AstrBot boundary.

Everything downstream sees :class:`Inbound`, never the raw ``AstrMessageEvent``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from alicedev.github.parser import parse_github_ref

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

_LOG = logging.getLogger("alicedev.actions.inbound")

# Every AI message starts with its session marker "%<no> <name>" (outbox render).
_MARKER = re.compile(r"^\s*[%％]\s*(\d+)\b")


@dataclass(frozen=True)
class QuotedMessage:
    platform_message_id: str
    sender_key: str
    sender_name: str
    text: str
    image_urls: tuple[str, ...] = ()

    @property
    def session_marker(self) -> int | None:
        """The ``%n`` a bot message starts with, if any."""
        match = _MARKER.match(self.text or "")
        return int(match.group(1)) if match else None


@dataclass(frozen=True)
class Mention:
    user_key: str
    platform_id: str
    name: str


@dataclass(frozen=True)
class Inbound:
    chat_key: str
    platform_id: str
    user_key: str
    sender_id: str
    sender_name: str
    chat_name: str
    text: str
    platform_message_id: str | None
    is_admin: bool
    addressed: bool
    quoted: QuotedMessage | None = None
    mentions: tuple[Mention, ...] = field(default_factory=tuple)
    images: tuple[str, ...] = ()

    @property
    def is_slash(self) -> bool:
        return self.text.lstrip().startswith(("/", "／"))

    def base_vars(self) -> dict[str, Any]:
        """BASE_VARS of the DSL: sender, chat, quoted, images."""
        return {
            "sender": {"id": self.user_key, "name": self.sender_name},
            "chat": {"key": self.chat_key, "name": self.chat_name},
            "quoted": (
                {"sender": self.quoted.sender_name, "text": self.quoted.text,
                 "images": list(self.quoted.image_urls)}
                if self.quoted else None
            ),
            "images": list(self.images),
        }


def exact_github_url(text: str) -> bool:
    """A message that is exactly one GitHub issue/PR URL (route ``github_link``)."""
    value = text.strip()
    if not value or any(ch.isspace() for ch in value):
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
    return ref is not None and getattr(ref, "kind", None) is not None


def _components(event: "AstrMessageEvent") -> list:
    try:
        return list(event.get_messages() or [])
    except Exception:  # noqa: BLE001
        return []


def from_event(event: "AstrMessageEvent", *, admin_users: tuple[str, ...]) -> Inbound:
    platform_id = str(event.get_platform_id())
    sender_id = str(event.get_sender_id())
    user_key = f"{platform_id}:{sender_id}"

    raw = ""
    try:
        raw = event.message_obj.message_str or ""
    except Exception:  # noqa: BLE001
        raw = ""
    stripped = (event.message_str or "").strip()
    text = raw.strip() if raw.strip()[:1] in ("/", "／") else stripped
    if not text and raw.strip():
        text = raw.strip()
    woke = bool(getattr(event, "is_at_or_wake_command", False))
    # A waking stage may strip the slash prefix; restore it so the DSL sees "/cmd".
    if woke and raw.strip()[:1] in ("/", "／") and not text.startswith(("/", "／")):
        text = raw.strip()

    quoted: QuotedMessage | None = None
    mentions: list[Mention] = []
    images: list[str] = []
    for comp in _components(event):
        kind = type(comp).__name__
        if kind == "Reply" and quoted is None:
            q_images = tuple(
                str(getattr(c, "url", "") or getattr(c, "file", ""))
                for c in (getattr(comp, "chain", None) or [])
                if type(c).__name__ == "Image"
            )
            q_sender = str(getattr(comp, "sender_id", "") or "")
            quoted = QuotedMessage(
                platform_message_id=str(getattr(comp, "id", "") or ""),
                sender_key=f"{platform_id}:{q_sender}" if q_sender else "",
                sender_name=str(getattr(comp, "sender_nickname", "") or ""),
                text=str(getattr(comp, "message_str", "") or ""),
                image_urls=tuple(u for u in q_images if u),
            )
        elif kind == "At":
            qq = str(getattr(comp, "qq", "") or "")
            if qq and qq != str(getattr(event.message_obj, "self_id", "")):
                mentions.append(Mention(user_key=f"{platform_id}:{qq}", platform_id=qq,
                                        name=str(getattr(comp, "name", "") or "")))
        elif kind == "Image":
            url = str(getattr(comp, "url", "") or getattr(comp, "file", "") or "")
            if url:
                images.append(url)

    chat_name = event.unified_msg_origin
    try:
        group = event.message_obj.group
        if group is not None and getattr(group, "group_name", None):
            chat_name = str(group.group_name)
    except Exception:  # noqa: BLE001
        pass

    private = False
    checker = getattr(event, "is_private_chat", None)
    try:
        private = bool(checker() if callable(checker) else checker)
    except Exception:  # noqa: BLE001
        private = False

    message_id: str | None
    try:
        message_id = str(event.message_obj.message_id) if event.message_obj.message_id else None
    except Exception:  # noqa: BLE001
        message_id = None

    return Inbound(
        chat_key=event.unified_msg_origin,
        platform_id=platform_id,
        user_key=user_key,
        sender_id=sender_id,
        sender_name=str(event.get_sender_name() or ""),
        chat_name=str(chat_name),
        text=text,
        platform_message_id=message_id,
        is_admin=user_key in admin_users,
        addressed=woke or private,
        quoted=quoted,
        mentions=tuple(mentions),
        images=tuple(images),
    )
