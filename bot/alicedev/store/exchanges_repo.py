"""Recent human messages and agent replies for a user-visible session."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Mapping, assert_never

from alicedev.api.payloads import (
    FileReply,
    ImageReply,
    ImageTemplateReply,
    StickerReply,
    TextReply,
    TextTemplateReply,
    parse_reply_payload,
)

if TYPE_CHECKING:
    from alicedev.store.db import Store


@dataclass(frozen=True)
class Exchange:
    id: str
    kind: Literal["human", "ai"]
    session_id: int
    chat_key: str
    author_key: str | None
    author_name: str
    text: str
    images: tuple[str, ...]
    at: datetime


# Keep the two projections identical so recent() and get() share decoding.
_HUMAN = """
    SELECT m.msg_ref AS id, 'human' AS kind, m.session_id, m.chat_key,
           m.sender_key AS author_key,
           COALESCE(NULLIF(m.sender_name, ''), m.sender_key, '') AS author_name,
           m.content AS text, NULL AS payload, m.created_at AS created_at,
           0 AS source_rank, m.msg_ref AS source_id
    FROM messages AS m
"""
_AI = """
    SELECT o.reply_id AS id, 'ai' AS kind, o.session_id, o.chat_key,
           NULL AS author_key,
           '%' || CAST(s.no AS VARCHAR) || ' ' || s.name AS author_name,
           NULL AS text, CAST(o.payload AS VARCHAR) AS payload, o.created_at AS created_at,
           1 AS source_rank, o.reply_id AS source_id
    FROM outbox AS o
    JOIN sessions AS s ON s.session_id = o.session_id
"""
_AI_REPLY = """
    o.agent_ref IS NOT NULL AND o.state IN ('queued', 'sent')
    AND json_extract_string(o.payload, '$.type') = 'ai'
    AND json_type(o.payload, '$.reply') = 'OBJECT'
"""


def _readable_reply(payload: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    reply = parse_reply_payload(payload["reply"])
    match reply:
        case TextReply(text=text):
            return text, ()
        case TextTemplateReply(fields=fields):
            return "\n".join(fields.values()), ()
        case ImageTemplateReply(fields=fields):
            return "\n".join(fields[key] for key in ("title", "text")
                             if isinstance(fields.get(key), str)), ()
        case ImageReply(paths=paths, caption=caption):
            return caption or "", paths
        case StickerReply():
            return "[表情]", ()
        case FileReply(path=path, caption=caption):
            published = payload.get("published")
            if isinstance(published, Mapping):
                url = published.get("url")
                basename = published.get("basename")
            else:
                url = basename = None
            label = url if isinstance(url, str) and url else (
                basename if isinstance(basename, str) and basename else Path(path).name
            )
            return "\n".join(part for part in (caption, label) if part), ()
        case _ as unreachable:
            assert_never(unreachable)


def _exchange(row: tuple[Any, ...]) -> Exchange:
    kind = row[1]
    if kind == "human":
        return Exchange(
            id=row[0], kind="human", session_id=row[2], chat_key=row[3],
            author_key=row[4], author_name=row[5], text=row[6], images=(), at=row[8],
        )
    if kind == "ai":
        text, images = _readable_reply(json.loads(row[7]))
        return Exchange(
            id=row[0], kind="ai", session_id=row[2], chat_key=row[3],
            author_key=None, author_name=row[5], text=text, images=images, at=row[8],
        )
    raise ValueError(f"unknown exchange kind: {kind!r}")


class ExchangesRepo:
    def __init__(self, store: Store) -> None:
        self._store = store

    async def recent(self, session_id: int, limit: int = 10) -> list[Exchange]:
        if limit <= 0:
            return []
        rows = await self._store.fetch_all(
            f"SELECT * FROM ({_HUMAN} WHERE m.session_id = ? AND m.content IS NOT NULL "
            f"UNION ALL {_AI} WHERE o.session_id = ? AND {_AI_REPLY}) AS exchanges "
            "ORDER BY created_at DESC, source_rank DESC, source_id DESC LIMIT ?",
            (session_id, session_id, limit),
        )
        return [_exchange(row) for row in reversed(rows)]

    async def get(self, item_id: str) -> Exchange | None:
        if item_id.startswith("m_"):
            row = await self._store.fetch_one(
                f"{_HUMAN} WHERE m.msg_ref = ? AND m.content IS NOT NULL", (item_id,)
            )
        else:
            row = await self._store.fetch_one(
                f"{_AI} WHERE o.reply_id = ? AND {_AI_REPLY}", (item_id,)
            )
        return _exchange(row) if row is not None else None
