"""ReplyPayload ADT and boundary parsing (ARCHITECTURE §6).

The reply-cli posts an untyped JSON body; :func:`parse_reply_payload` turns it
into a precise domain variant or raises :class:`InvalidPayload`. Internal code
only ever handles the parsed variants.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Union


class InvalidPayload(ValueError):
    """The reply payload is malformed (maps to HTTP 400 invalid_payload)."""


@dataclass(frozen=True)
class TextReply:
    text: str
    sticker: str | None = None
    kind: str = "text"


@dataclass(frozen=True)
class TextTemplateReply:
    template: str
    fields: Mapping[str, str]
    sticker: str | None = None
    kind: str = "text_template"


@dataclass(frozen=True)
class ImageTemplateReply:
    template: str
    fields: Mapping[str, Any]
    sticker: str | None = None
    kind: str = "image_template"


@dataclass(frozen=True)
class StickerReply:
    sticker: str
    kind: str = "sticker"


@dataclass(frozen=True)
class FileReply:
    path: str
    caption: str | None = None
    kind: str = "file"


ReplyPayload = Union[
    TextReply, TextTemplateReply, ImageTemplateReply, StickerReply, FileReply
]


def parse_reply_payload(raw: Any) -> ReplyPayload:
    if not isinstance(raw, Mapping):
        raise InvalidPayload("reply must be an object")
    kind = raw.get("kind")
    match kind:
        case "text":
            text = raw.get("text")
            if not isinstance(text, str):
                raise InvalidPayload("text.text must be a string")
            return TextReply(text=text, sticker=_opt_str(raw, "sticker"))
        case "text_template":
            return TextTemplateReply(
                template=_req_str(raw, "template"),
                fields=_str_map(raw.get("fields")),
                sticker=_opt_str(raw, "sticker"),
            )
        case "image_template":
            fields = raw.get("fields")
            if not isinstance(fields, Mapping):
                raise InvalidPayload("image_template.fields must be an object")
            return ImageTemplateReply(
                template=_req_str(raw, "template"),
                fields=dict(fields),
                sticker=_opt_str(raw, "sticker"),
            )
        case "sticker":
            return StickerReply(sticker=_req_str(raw, "sticker"))
        case "file":
            return FileReply(path=_req_str(raw, "path"), caption=_opt_str(raw, "caption"))
        case _:
            raise InvalidPayload(f"unknown reply kind: {kind!r}")


def payload_digest(raw: Any) -> str:
    """Stable sha256 over the reply payload for idempotency comparison."""
    canonical = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _req_str(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise InvalidPayload(f"{key} must be a non-empty string")
    return value


def _opt_str(raw: Mapping[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidPayload(f"{key} must be a string")
    return value


def _str_map(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise InvalidPayload("fields must be an object")
    return {str(k): str(v) for k, v in value.items()}
