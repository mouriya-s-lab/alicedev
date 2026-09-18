"""Reply rendering: ReplyPayload -> AstrBot message chain (ARCHITECTURE §6).

Enforces the reply spec (allowed kinds/templates/stickers), the long-text
invariant (``text`` over ``max_text_chars`` becomes a ``generic_card`` image when
a :class:`CardRenderer` is present), and file publishing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from alicedev.api.payloads import (
    FileReply,
    ImageTemplateReply,
    ReplyPayload,
    StickerReply,
    TextReply,
    TextTemplateReply,
)

if TYPE_CHECKING:
    from alicedev.config import PluginConfig
    from alicedev.render.cards import CardRenderer
    from alicedev.reports import ReportPublisher
    from alicedev.templates.registry import ReplySpec, TemplateRegistry

_LOG = logging.getLogger("alicedev.api.reply")

_GENERIC_CARD = "generic_card"


class ReplyValidationError(ValueError):
    """A reply violates the session reply spec (maps to 400 kind_not_allowed / template_unknown)."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error


@dataclass
class ReplyRenderResult:
    components: list[Any] = field(default_factory=list)
    report_url: str | None = None


class ReplyRenderer:
    def __init__(
        self,
        *,
        config: "PluginConfig",
        templates: "TemplateRegistry",
        render: "CardRenderer | None",
        reports: "ReportPublisher",
    ) -> None:
        self._config = config
        self._templates = templates
        self._render = render
        self._reports = reports

    def validate(self, payload: ReplyPayload, spec: "ReplySpec") -> None:
        kind = payload.kind
        # The long-text invariant may upgrade text -> image_template internally,
        # so a plain over-long text is always acceptable.
        if not spec.allows(kind) and kind != "text":
            raise ReplyValidationError("kind_not_allowed")
        if isinstance(payload, ImageTemplateReply):
            if payload.template not in spec.image_templates and payload.template != _GENERIC_CARD:
                raise ReplyValidationError("template_unknown")
        if isinstance(payload, TextTemplateReply):
            if payload.template not in spec.text_templates:
                raise ReplyValidationError("template_unknown")

    async def render(self, payload: ReplyPayload, spec: "ReplySpec") -> ReplyRenderResult:
        from astrbot.api.message_components import Image, Plain  # local: astrbot-only

        result = ReplyRenderResult()
        match payload:
            case TextReply(text=text, sticker=sticker):
                if len(text) > spec.max_text_chars and self._render is not None:
                    path = await self._render.render(_GENERIC_CARD, {"text": text})
                    result.components.append(Image.fromFileSystem(str(path)))
                else:
                    result.components.append(Plain(text))
                self._append_sticker(result, sticker, Image)
            case TextTemplateReply(template=template, fields=fields, sticker=sticker):
                text = self._render_text_template(template, fields)
                result.components.append(Plain(text))
                self._append_sticker(result, sticker, Image)
            case ImageTemplateReply(template=template, fields=fields, sticker=sticker):
                if self._render is None:
                    result.components.append(Plain(_fields_fallback(fields)))
                else:
                    path = await self._render.render(template, fields)
                    result.components.append(Image.fromFileSystem(str(path)))
                self._append_sticker(result, sticker, Image)
            case StickerReply(sticker=sticker):
                self._append_sticker(result, sticker, Image, required=True)
            case FileReply(path=path, caption=caption):
                await self._render_file(result, path, caption, Image, Plain)
        return result

    def _render_text_template(self, template: str, fields: Any) -> str:
        tpl = self._templates.by_name(template)
        if tpl is None:
            raise ReplyValidationError("template_unknown")
        import jinja2

        return jinja2.Environment(autoescape=False).from_string(tpl.body).render(**dict(fields))

    def _append_sticker(self, result: ReplyRenderResult, sticker: str | None, Image, *, required: bool = False) -> None:
        if not sticker:
            if required:
                raise ReplyValidationError("invalid_payload")
            return
        path = self._sticker_path(sticker)
        if path is not None:
            result.components.append(Image.fromFileSystem(str(path)))
        elif required:
            raise ReplyValidationError("invalid_payload")

    def _sticker_path(self, name: str) -> Path | None:
        root = self._config.stickers_root
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            candidate = root / f"{name}{ext}"
            if candidate.is_file():
                return candidate
        return None

    async def _render_file(self, result: ReplyRenderResult, path: str, caption: str | None, Image, Plain) -> None:
        published = await self._reports.publish(path)
        if caption:
            result.components.append(Plain(caption + "\n"))
        if published.is_markdown:
            result.report_url = published.url
            result.components.append(Plain(published.url))
        else:
            from astrbot.api.message_components import File  # local import

            result.components.append(
                File(name=published.basename, file=str(published.published_path))
            )


def _fields_fallback(fields: Any) -> str:
    parts = [f"{k}: {v}" for k, v in dict(fields).items()]
    return "\n".join(parts)
