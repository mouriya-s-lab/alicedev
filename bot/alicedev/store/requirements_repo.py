"""Typed access to the ``requirements`` table."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from alicedev.render.pagination import DEFAULT_PAGE_SIZE, Page, clamp_page, page_count, parse_page

if TYPE_CHECKING:
    from alicedev.store.db import Store


@dataclass(frozen=True)
class RequirementRecord:
    id: int
    chat_key: str
    session_ref: str | None
    author_key: str
    author_name: str
    text: str
    images: tuple[str, ...]
    status: str
    created_at: datetime | None


def _images_from_db(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    raw: object = value
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("requirements.images is not valid JSON") from exc
    if not isinstance(raw, (list, tuple)):
        raise ValueError("requirements.images must be a JSON array")
    return tuple(str(item) for item in raw)


def _row_to_record(row: tuple) -> RequirementRecord:
    return RequirementRecord(
        id=int(row[0]),
        chat_key=str(row[1]),
        session_ref=str(row[2]) if row[2] is not None else None,
        author_key=str(row[3]),
        author_name=str(row[4]),
        text=str(row[5]),
        images=_images_from_db(row[6]),
        status=str(row[7]),
        created_at=row[8],
    )


_COLUMNS = (
    "id, chat_key, session_ref, author_key, author_name, text, images, status, created_at"
)


class RequirementsRepo:
    """Repository using the process-wide single-writer :class:`Store`."""

    def __init__(self, store: "Store") -> None:
        self._store = store

    async def insert(
        self,
        chat_key: str,
        session_ref: str | None,
        author_key: str,
        author_name: str,
        text: str,
        images: list[str],
        status: str = "open",
    ) -> int:
        """Insert a requirement and return its stable numeric id."""

        encoded_images = json.dumps(images, ensure_ascii=False)
        async with self._store.lock:
            record_id = await self._store.next_id("seq_requirements")
            await self._store.execute(
                "INSERT INTO requirements "
                "(id, chat_key, session_ref, author_key, author_name, text, images, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record_id,
                    chat_key,
                    session_ref,
                    author_key,
                    author_name,
                    text,
                    encoded_images,
                    status,
                ),
            )
        return record_id

    async def get(self, record_id: int) -> RequirementRecord | None:
        row = await self._store.fetch_one(
            f"SELECT {_COLUMNS} FROM requirements WHERE id = ?", (record_id,)
        )
        return _row_to_record(row) if row else None

    async def count(self, chat_key: str) -> int:
        row = await self._store.fetch_one(
            "SELECT COUNT(*) FROM requirements WHERE chat_key = ?", (chat_key,)
        )
        return int(row[0]) if row else 0

    async def list_page(
        self,
        chat_key: str,
        page: int | str | None = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> Page[RequirementRecord]:
        """Read a chronological, clamped page for one chat."""

        if page_size <= 0:
            raise ValueError("page_size must be positive")
        requested = parse_page(page) if isinstance(page, str) or page is None else max(1, page)
        async with self._store.lock:
            total_row = await self._store.fetch_one(
                "SELECT COUNT(*) FROM requirements WHERE chat_key = ?", (chat_key,)
            )
            total = int(total_row[0]) if total_row else 0
            pages = page_count(total, page_size)
            current = clamp_page(requested, pages)
            rows = await self._store.fetch_all(
                f"SELECT {_COLUMNS} FROM requirements WHERE chat_key = ? "
                "ORDER BY id ASC LIMIT ? OFFSET ?",
                (chat_key, page_size, (current - 1) * page_size),
            )
        return Page(
            items=[_row_to_record(row) for row in rows],
            page=current,
            pages=pages,
            total=total,
            page_size=page_size,
        )
