"""Requirement facts, distinct from sessions and favorites (ARCHITECTURE §10)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from alicedev.domain import JsonValue
from alicedev.render.pagination import DEFAULT_PAGE_SIZE, Page, clamp_page, page_count

if TYPE_CHECKING:
    from alicedev.store.db import Store


class RequirementSourceKind(str, Enum):
    V2_REQUIREMENT = "v2_requirement"
    REQUIREMENT_SESSION = "requirement_session"
    CHAT_MESSAGE = "chat_message"
    AGENT_CALL = "agent_call"


@dataclass(frozen=True)
class RequirementSource:
    kind: RequirementSourceKind
    ref: str


@dataclass(frozen=True)
class RequirementQuote:
    author_key: str | None
    author_name: str
    text: str
    images: tuple[str, ...]


@dataclass(frozen=True)
class NewRequirement:
    chat_key: str
    author_key: str
    author_name: str
    text: str
    images: tuple[str, ...]
    quoted: RequirementQuote | None
    source: RequirementSource


@dataclass(frozen=True)
class RequirementRecord:
    id: int
    chat_key: str
    author_key: str
    author_name: str
    text: str
    images: tuple[str, ...]
    quoted: RequirementQuote | None
    status: str  # historical source vocabulary is retained; new records are open
    source: RequirementSource
    created_at: datetime


class SaveDisposition(str, Enum):
    CREATED = "created"
    REPLAYED = "replayed"


@dataclass(frozen=True)
class SavedRequirement:
    record: RequirementRecord
    disposition: SaveDisposition


def image_paths(value: JsonValue) -> tuple[str, ...]:
    """Parse stored image JSON once at the persistence/migration boundary."""
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("requirements.images must be a string array")
    return tuple(value)


def _quote(value: JsonValue) -> RequirementQuote | None:
    if value is None:
        return None
    if not isinstance(value, dict) or value.keys() != {"author_key", "author_name", "text", "images"}:
        raise ValueError("requirements.quoted must be a quote record")
    key, name, text = value["author_key"], value["author_name"], value["text"]
    if key is not None and not isinstance(key, str):
        raise ValueError("requirements.quoted.author_key must be a string or null")
    if not isinstance(name, str) or not isinstance(text, str):
        raise ValueError("requirements.quoted name and text must be strings")
    return RequirementQuote(key, name, text, image_paths(value["images"]))


def quote_json(quote: RequirementQuote | None) -> JsonValue:
    if quote is None:
        return None
    return {"author_key": quote.author_key, "author_name": quote.author_name,
            "text": quote.text, "images": list(quote.images)}


RequirementDbRow = tuple[int, str, str, str, str, str | None, str | None, str, str, str, datetime]


def _record(row: RequirementDbRow) -> RequirementRecord:
    images: JsonValue = json.loads(row[5]) if row[5] is not None else None
    quoted: JsonValue = json.loads(row[6]) if row[6] is not None else None
    return RequirementRecord(row[0], row[1], row[2], row[3], row[4], image_paths(images),
                             _quote(quoted), row[7], RequirementSource(RequirementSourceKind(row[8]), row[9]), row[10])


_COLUMNS = "id, chat_key, author_key, author_name, text, images, quoted, status, source_kind, source_ref, created_at"


class RequirementsRepo:
    def __init__(self, store: Store) -> None:
        self._store = store

    async def get(self, record_id: int) -> RequirementRecord | None:
        row = await self._store.fetch_one(f"SELECT {_COLUMNS} FROM requirements WHERE id = ?", (record_id,))
        return _record(row) if row is not None else None

    async def by_source(self, chat_key: str, source: RequirementSource) -> RequirementRecord | None:
        row = await self._store.fetch_one(
            f"SELECT {_COLUMNS} FROM requirements WHERE chat_key = ? AND source_kind = ? AND source_ref = ?",
            (chat_key, source.kind.value, source.ref),
        )
        return _record(row) if row is not None else None

    async def save(self, value: NewRequirement) -> SavedRequirement:
        async with self._store.lock:
            existing = await self.by_source(value.chat_key, value.source)
            if existing is not None:
                return SavedRequirement(existing, SaveDisposition.REPLAYED)
            record_id = await self._store.next_id("seq_requirements")
            await self._store.execute(
                "INSERT INTO requirements (id, chat_key, author_key, author_name, text, images, quoted, status, source_kind, source_ref) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                (record_id, value.chat_key, value.author_key, value.author_name, value.text,
                 json.dumps(list(value.images), ensure_ascii=False), json.dumps(quote_json(value.quoted), ensure_ascii=False),
                 value.source.kind.value, value.source.ref),
            )
            record = await self.get(record_id)
            assert record is not None
            return SavedRequirement(record, SaveDisposition.CREATED)

    async def list_page(self, chat_key: str, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> Page[RequirementRecord]:
        if page_size < 1:
            raise ValueError("page_size must be positive")
        async with self._store.lock:
            count = await self._store.fetch_one("SELECT COUNT(*) FROM requirements WHERE chat_key = ?", (chat_key,))
            total = int(count[0]) if count is not None else 0
            pages = page_count(total, page_size)
            current = clamp_page(max(1, page), pages)
            rows = await self._store.fetch_all(
                f"SELECT {_COLUMNS} FROM requirements WHERE chat_key = ? ORDER BY id ASC LIMIT ? OFFSET ?",
                (chat_key, page_size, (current - 1) * page_size),
            )
        return Page([_record(row) for row in rows], current, pages, total, page_size)
