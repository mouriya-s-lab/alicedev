"""Domain values returned by tools; rendering and HTTP encoding stay outside."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from alicedev.agent_tools.model import ToolSpec
from alicedev.domain import SessionView
from alicedev.render.pagination import Page
from alicedev.scheduler.engine import StartOutcome
from alicedev.status import BotStatus
from alicedev.store.exchanges_repo import Exchange
from alicedev.store.favorites_repo import FavoriteRecord
from alicedev.store.requirements_repo import RequirementRecord
from alicedev.store.sessions_repo import KnownChat


class ToolResult(str, Enum):
    OK = "ok"
    QUEUED = "queued"
    NOT_READY = "not_ready"


@dataclass(frozen=True)
class ToolDiscovery:
    agent: str
    session_no: int
    chat_key: str
    tools: tuple[ToolSpec, ...]


@dataclass(frozen=True)
class SessionDetails:
    session: SessionView
    recent: tuple[Exchange, ...]


@dataclass(frozen=True)
class Chats:
    known: tuple[KnownChat, ...]
    allowed: tuple[str, ...]


@dataclass(frozen=True)
class RecordReceipt:
    id: int


@dataclass(frozen=True)
class StartReceipt:
    outcome: StartOutcome
    session: SessionView | None


@dataclass(frozen=True)
class LinkReceipt:
    delivery_id: str
    session_no: int


ToolData = (SessionDetails | Page[SessionView] | Chats | BotStatus | Page[RequirementRecord]
            | Page[FavoriteRecord] | RecordReceipt | StartReceipt | SessionView | LinkReceipt | None)


@dataclass(frozen=True)
class ToolSuccess:
    result: ToolResult
    data: ToolData
