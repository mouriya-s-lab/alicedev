"""Outbox payload → AstrBot message chain (ARCHITECTURE §6 出队).

Payloads are tagged dicts stored in ``outbox.payload``:

* ``{"type": "ai", "reply": <ReplyPayload JSON>, "marker": "%3 名称",
  "max_text_chars": int, "published": {...}|null}`` — an agent's reply.
  Every AI message carries the session marker as a text line (prefix of a text
  message, a line next to an image/card/file) so a quoted bot message can be
  resolved back to its session from the quote's text (AstrBot proactive sends
  return no platform message id).
* ``{"type": "text", "text": str}``
* ``{"type": "card", "card": str, "fields": {...}, "caption": str|null}``
* ``{"type": "at_text", "platform_id": str, "name": str, "text": str}`` — an @ mention
  plus text; in a private chat (QQ rejects ``at`` there) only the text is sent.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

import jinja2

from alicedev.images import ImageDownloadError, image_data_uri

if TYPE_CHECKING:
    from alicedev.config import PluginConfig
    from alicedev.render.cards import CardRenderer

_LOG = logging.getLogger("alicedev.outbox.render")
_GENERIC_CARD = "generic_card"


class RenderError(RuntimeError):
    """A payload could not be rendered (never retried as-is)."""


def is_private_chat(chat_key: str) -> bool:
    """AstrBot's ``unified_msg_origin`` is ``<platform>:<MessageType>:<session>``; ``FriendMessage`` is private."""
    parts = chat_key.split(":", 2)
    return len(parts) == 3 and parts[1] == "FriendMessage"


class OutboxRenderer:
    def __init__(self, *, config: "PluginConfig", cards: "CardRenderer | None") -> None:
        self._config = config
        self._cards = cards

    async def render(self, payload: Mapping[str, Any], *, private: bool = False) -> list[Any]:
        from astrbot.api.message_components import At, File, Image, Plain  # astrbot-only

        match payload.get("type"):
            case "text":
                return [Plain(str(payload["text"]))]
            case "at_text":
                if private:  # nobody to mention in a one-to-one chat, and QQ refuses `at` there
                    return [Plain(str(payload["text"]).lstrip())]
                return [At(qq=str(payload["platform_id"]), name=str(payload.get("name") or "")),
                        Plain(str(payload["text"]))]
            case "card":
                components: list[Any] = []
                if payload.get("caption"):
                    components.append(Plain(str(payload["caption"])))
                components.append(Image.fromFileSystem(str(
                    await self._card(str(payload["card"]), dict(payload.get("fields") or {}))
                )))
                return components
            case "ai":
                return await self._render_ai(payload, Plain=Plain, Image=Image, File=File)
            case other:
                raise RenderError(f"unknown outbox payload type {other!r}")

    async def _card(self, card: str, fields: dict[str, Any]) -> Path:
        if self._cards is None:
            raise RenderError("card renderer unavailable")
        return await self._cards.render(card, fields)

    async def _render_ai(self, payload: Mapping[str, Any], *, Plain, Image, File) -> list[Any]:
        reply = dict(payload.get("reply") or {})
        marker = str(payload.get("marker") or "").strip()
        head = f"{marker}\n" if marker else ""
        max_chars = int(payload.get("max_text_chars") or 600)
        out: list[Any] = []
        match reply.get("kind"):
            case "text":
                text = str(reply.get("text", ""))
                if len(text) > max_chars and self._cards is not None:
                    out.append(Plain(marker or " "))
                    out.append(Image.fromFileSystem(str(await self._card(_GENERIC_CARD, {"text": text}))))
                else:
                    out.append(Plain(head + text))
            case "text_template":
                out.append(Plain(head + self._text_template(str(reply["template"]),
                                                            dict(reply.get("fields") or {}))))
            case "image_template":
                if marker:
                    out.append(Plain(marker))
                fields = dict(reply.get("fields") or {})
                if self._cards is None:
                    out.append(Plain("\n".join(f"{k}: {v}" for k, v in fields.items())))
                else:
                    out.append(Image.fromFileSystem(str(await self._card(str(reply["template"]), fields))))
            case "image":
                caption = str(reply.get("caption") or "")
                out.append(Plain((head + caption).strip() or marker or " "))
                for path in reply.get("paths") or []:
                    out.append(self._inline_image(str(path), Image))
            case "sticker":
                if marker:
                    out.append(Plain(marker))
            case "file":
                published = dict(payload.get("published") or {})
                caption = str(reply.get("caption") or "")
                if published.get("is_markdown") and published.get("url"):
                    out.append(Plain(head + (caption + "\n" if caption else "") + str(published["url"])))
                else:
                    out.append(Plain((head + caption).strip() or marker or " "))
                    out.append(File(name=str(published.get("basename") or "file"),
                                    file=str(published.get("path") or reply.get("path"))))
            case other:
                raise RenderError(f"unknown reply kind {other!r}")
        sticker = reply.get("sticker")
        if sticker:
            path = self._sticker_path(str(sticker))
            if path is not None:
                out.append(Image.fromFileSystem(str(path)))
        return out

    def _inline_image(self, path: str, Image) -> Any:
        """``Image`` from inline base64 so it is sent as an image, not a file."""
        try:
            uri = image_data_uri(Path(path))
        except ImageDownloadError as exc:
            raise RenderError(f"image not readable: {path}") from exc
        _prefix, sep, encoded = uri.partition(",")
        if not sep or not encoded:
            raise RenderError(f"image not readable: {path}")
        return Image.fromBase64(encoded)

    def _text_template(self, name: str, fields: Mapping[str, Any]) -> str:
        path = self._config.templates_root / "text" / f"{Path(name).name}.md"
        if not path.is_file():
            raise RenderError(f"text template {name!r} not found")
        env = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
        return env.from_string(path.read_text(encoding="utf-8")).render(**dict(fields))

    def _sticker_path(self, name: str) -> Path | None:
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            candidate = self._config.stickers_root / f"{Path(name).name}{ext}"
            if candidate.is_file():
                return candidate
        return None

    def sticker_exists(self, name: str) -> bool:
        return self._sticker_path(name) is not None

    def text_template_exists(self, name: str) -> bool:
        return (self._config.templates_root / "text" / f"{Path(name).name}.md").is_file()
