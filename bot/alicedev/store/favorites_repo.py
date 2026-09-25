"""Typed access to the ``favorites`` table."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from alicedev.render.pagination import DEFAULT_PAGE_SIZE, Page, clamp_page, page_count, parse_page

if TYPE_CHECKING:
    from alicedev.store.db import Store


@dataclass(frozen=True)
class FavoriteRecord:
    id: int
    chat_key: str
    saver_key: str
    saver_name: str
    author_key: str
    author_name: str
    text: str
    images: tuple[str, ...]
    platform_message_id: str | None
    created_at: datetime | None


def _images_from_db(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    raw: object = value
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("favorites.images is not valid JSON") from exc
    if not isinstance(raw, (list, tuple)):
        raise ValueError("favorites.images must be a JSON array")
    return tuple(str(item) for item in raw)


def _row_to_record(row: tuple) -> FavoriteRecord:
    return FavoriteRecord(
        id=int(row[0]),
        chat_key=str(row[1]),
        saver_key=str(row[2]),
        saver_name=str(row[3]),
        author_key=str(row[4]),
        author_name=str(row[5]),
        text=str(row[6]),
        images=_images_from_db(row[7]),
        platform_message_id=str(row[8]) if row[8] is not None else None,
        created_at=row[9],
    )


_COLUMNS = (
    "id, chat_key, saver_key, saver_name, author_key, author_name, text, "
    "images, platform_message_id, created_at"
)


class FavoritesRepo:
    """Repository using the process-wide single-writer :class:`Store`."""

    def __init__(self, store: "Store") -> None:
        self._store = store

    async def insert(
        self,
        chat_key: str,
        saver_key: str,
        saver_name: str,
        author_key: str,
        author_name: str,
        text: str,
        images: list[str],
        platform_message_id: str | None = None,
    ) -> int:
        """Insert a favorite and return its stable numeric id."""

        encoded_images = json.dumps(images, ensure_ascii=False)
        async with self._store.lock:
            record_id = await self._store.next_id("seq_favorites")
            await self._store.execute(
                "INSERT INTO favorites "
                "(id, chat_key, saver_key, saver_name, author_key, author_name, text, "
                "images, platform_message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record_id,
                    chat_key,
                    saver_key,
                    saver_name,
                    author_key,
                    author_name,
                    text,
                    encoded_images,
                    platform_message_id,
                ),
            )
        return record_id

    async def get(self, record_id: int) -> FavoriteRecord | None:
        row = await self._store.fetch_one(
            f"SELECT {_COLUMNS} FROM favorites WHERE id = ?", (record_id,)
        )
        return _row_to_record(row) if row else None
    async def by_platform_message_id(
        self, chat_key: str, platform_message_id: str
    ) -> FavoriteRecord | None:
        """Find the newest favorite for a platform message in one chat."""
        row = await self._store.fetch_one(
            f"SELECT {_COLUMNS} FROM favorites "
            "WHERE chat_key = ? AND platform_message_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (chat_key, platform_message_id),
        )
        return _row_to_record(row) if row is not None else None

    async def count(self, chat_key: str) -> int:
        row = await self._store.fetch_one(
            "SELECT COUNT(*) FROM favorites WHERE chat_key = ?", (chat_key,)
        )
        return int(row[0]) if row else 0

    async def list_page(
        self,
        chat_key: str,
        page: int | str | None = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> Page[FavoriteRecord]:
        """Read a chronological, clamped page for one chat."""

        if page_size <= 0:
            raise ValueError("page_size must be positive")
        requested = parse_page(page) if isinstance(page, str) or page is None else max(1, page)
        async with self._store.lock:
            total_row = await self._store.fetch_one(
                "SELECT COUNT(*) FROM favorites WHERE chat_key = ?", (chat_key,)
            )
            total = int(total_row[0]) if total_row else 0
            pages = page_count(total, page_size)
            current = clamp_page(requested, pages)
            rows = await self._store.fetch_all(
                f"SELECT {_COLUMNS} FROM favorites WHERE chat_key = ? "
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
