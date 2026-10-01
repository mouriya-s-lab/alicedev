"""Runtime domain views shared by the scheduler, actions and card views."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from alicedev.store.requirements_repo import RequirementQuote

# JSON is a boundary type; application operations use named domain records.
JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True)
class SessionView:
    """A user-visible session (ARCHITECTURE §2) as shown in cards and replies."""

    session_id: int
    chat_key: str
    no: int  # per-chat session number, shown as %n
    name: str
    scenario: str
    state: str
    created_by: str
    created_at: datetime
    last_activity_at: datetime | None
    is_current: bool

    @property
    def label(self) -> str:
        return f"%{self.no}"


@dataclass(frozen=True)
class FavoriteView:
    id: int
    author_name: str
    saver_name: str
    text: str
    images: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True)
class RequirementView:
    id: int
    author_name: str
    text: str
    images: tuple[str, ...]
    quoted: RequirementQuote | None
    status: str
    created_at: datetime
