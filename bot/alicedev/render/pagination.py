"""Small, deterministic pagination primitives shared by list commands."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Generic, Sequence, TypeVar

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 10


@dataclass(frozen=True)
class Page(Generic[T]):
    """A bounded page of records.

    ``pages`` is always at least one, including for an empty result set. This
    keeps the footer useful (``第 1/1 页``) and avoids a zero-page special case
    in command handlers and templates.
    """

    items: list[T]
    page: int
    pages: int
    total: int
    page_size: int = DEFAULT_PAGE_SIZE


def parse_page(value: str | None) -> int:
    """Parse an optional command argument, defaulting invalid values to page 1."""

    if value is None:
        return 1
    token = value.strip()
    if not token:
        return 1
    try:
        parsed = int(token, 10)
    except ValueError:
        return 1
    return max(1, parsed)


def page_count(total: int, page_size: int = DEFAULT_PAGE_SIZE) -> int:
    """Return the number of pages for ``total`` records (minimum one)."""

    if page_size <= 0:
        raise ValueError("page_size must be positive")
    return max(1, ceil(max(0, total) / page_size))


def clamp_page(page: int, pages: int) -> int:
    """Clamp a one-based page number to the available page range."""

    if pages <= 0:
        raise ValueError("pages must be positive")
    return min(max(1, page), pages)


def paginate(
    items: Sequence[T], page: int | str | None = 1, *, page_size: int = DEFAULT_PAGE_SIZE
) -> Page[T]:
    """Paginate an in-memory sequence using one-based, clamped page numbers."""

    if page_size <= 0:
        raise ValueError("page_size must be positive")
    requested = parse_page(page) if isinstance(page, str) or page is None else max(1, page)
    total = len(items)
    pages = page_count(total, page_size)
    current = clamp_page(requested, pages)
    start = (current - 1) * page_size
    return Page(
        items=list(items[start : start + page_size]),
        page=current,
        pages=pages,
        total=total,
        page_size=page_size,
    )
