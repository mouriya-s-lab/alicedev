"""Closed agent capabilities, independent of chat commands (ARCHITECTURE §6.1)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Literal, TypeAlias


class ToolName(str, Enum):
    SESSION_GET = "session_get"
    SESSION_LIST = "session_list"
    CHATS_LIST = "chats_list"
    STATUS_GET = "status_get"
    REQUIREMENTS_LIST = "requirements_list"
    FAVORITES_LIST = "favorites_list"
    REQUIREMENT_ADD = "requirement_add"
    FAVORITE_ADD = "favorite_add"
    INVESTIGATION_START = "investigation_start"
    GITHUB_ANALYSIS_START = "github_analysis_start"
    SESSION_ARCHIVE = "session_archive"
    SESSION_RENAME = "session_rename"
    SESSION_LINK_DELIVER = "session_link_deliver"


ADMIN_TOOLS = frozenset((ToolName.CHATS_LIST, ToolName.STATUS_GET, ToolName.SESSION_LINK_DELIVER))


class ToolError(str, Enum):
    INVALID_PAYLOAD = "invalid_payload"
    UNKNOWN_TOOL = "unknown_tool"
    TOOL_NOT_ALLOWED = "tool_not_allowed"
    PERMISSION_DENIED = "permission_denied"
    CHAT_NOT_ALLOWED = "chat_not_allowed"
    NESTED_ASSIGNMENT = "nested_assignment"
    SELF_ARCHIVE = "self_archive"
    AGENT_UNKNOWN = "agent_unknown"
    SESSION_NOT_FOUND = "session_not_found"
    QUOTE_NOT_FOUND = "quote_not_found"
    AGENT_NOT_CURRENT = "agent_not_current"
    CALL_ID_CONFLICT = "call_id_conflict"
    IMAGE_FAILED = "image_failed"
    FETCH_FAILED = "fetch_failed"
    LINK_FAILED = "link_failed"


@dataclass(frozen=True)
class ToolFailure:
    error: ToolError
    status: int


@dataclass(frozen=True)
class SessionGet:
    name: ClassVar[ToolName] = ToolName.SESSION_GET
    no: int
    chat: str | None = None


@dataclass(frozen=True)
class SessionList:
    name: ClassVar[ToolName] = ToolName.SESSION_LIST
    page: int = 1
    include_ended: bool = False
    chat: str | None = None


@dataclass(frozen=True)
class ChatsList:
    name: ClassVar[ToolName] = ToolName.CHATS_LIST


@dataclass(frozen=True)
class StatusGet:
    name: ClassVar[ToolName] = ToolName.STATUS_GET


@dataclass(frozen=True)
class RequirementsList:
    name: ClassVar[ToolName] = ToolName.REQUIREMENTS_LIST
    page: int = 1
    chat: str | None = None


@dataclass(frozen=True)
class FavoritesList:
    name: ClassVar[ToolName] = ToolName.FAVORITES_LIST
    page: int = 1
    chat: str | None = None


@dataclass(frozen=True)
class RequirementAdd:
    name: ClassVar[ToolName] = ToolName.REQUIREMENT_ADD
    text: str
    chat: str | None = None
    quote: str | None = None


@dataclass(frozen=True)
class FavoriteAdd:
    name: ClassVar[ToolName] = ToolName.FAVORITE_ADD
    quote: str
    chat: str | None = None


@dataclass(frozen=True)
class InvestigationStart:
    name: ClassVar[ToolName] = ToolName.INVESTIGATION_START
    text: str
    chat: str | None = None


@dataclass(frozen=True)
class GithubAnalysisStart:
    name: ClassVar[ToolName] = ToolName.GITHUB_ANALYSIS_START
    url: str
    chat: str | None = None


@dataclass(frozen=True)
class SessionArchive:
    name: ClassVar[ToolName] = ToolName.SESSION_ARCHIVE
    no: int
    chat: str | None = None


@dataclass(frozen=True)
class SessionRename:
    name: ClassVar[ToolName] = ToolName.SESSION_RENAME
    no: int
    name_text: str
    chat: str | None = None


@dataclass(frozen=True)
class SessionLinkDeliver:
    name: ClassVar[ToolName] = ToolName.SESSION_LINK_DELIVER
    no: int
    chat: str | None = None


ToolInvocation: TypeAlias = (
    SessionGet | SessionList | ChatsList | StatusGet | RequirementsList | FavoritesList
    | RequirementAdd | FavoriteAdd | InvestigationStart | GithubAnalysisStart
    | SessionArchive | SessionRename | SessionLinkDeliver
)


@dataclass(frozen=True)
class ToolRequest:
    agent_ref: str
    call_id: str
    invocation: ToolInvocation


@dataclass(frozen=True)
class ToolParameter:
    name: str
    kind: Literal["string", "integer", "boolean"]
    required: bool = False


@dataclass(frozen=True)
class ToolSpec:
    name: ToolName
    summary: str
    parameters: tuple[ToolParameter, ...]


_CHAT = ToolParameter("chat", "string")
_PAGE = ToolParameter("page", "integer")
_NO = ToolParameter("no", "integer", True)
_TEXT = ToolParameter("text", "string", True)
_QUOTE = ToolParameter("quote", "string", True)

TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(ToolName.SESSION_GET, "Read a session and its recent exchanges", (_NO, _CHAT)),
    ToolSpec(ToolName.SESSION_LIST, "List sessions", (_PAGE, ToolParameter("include_ended", "boolean"), _CHAT)),
    ToolSpec(ToolName.CHATS_LIST, "List known and allowed chats", ()),
    ToolSpec(ToolName.STATUS_GET, "Read bot status", ()),
    ToolSpec(ToolName.REQUIREMENTS_LIST, "List independent requirement records", (_PAGE, _CHAT)),
    ToolSpec(ToolName.FAVORITES_LIST, "List saved exchanges", (_PAGE, _CHAT)),
    ToolSpec(ToolName.REQUIREMENT_ADD, "Record a requirement without starting AI", (_TEXT, _CHAT, ToolParameter("quote", "string"))),
    ToolSpec(ToolName.FAVORITE_ADD, "Save a real quoted exchange", (_QUOTE, _CHAT)),
    ToolSpec(ToolName.INVESTIGATION_START, "Assign an investigation", (_TEXT, _CHAT)),
    ToolSpec(ToolName.GITHUB_ANALYSIS_START, "Assign analysis of a GitHub issue or PR", (ToolParameter("url", "string", True), _CHAT)),
    ToolSpec(ToolName.SESSION_ARCHIVE, "End another session", (_NO, _CHAT)),
    ToolSpec(ToolName.SESSION_RENAME, "Rename a session", (_NO, ToolParameter("name", "string", True), _CHAT)),
    ToolSpec(ToolName.SESSION_LINK_DELIVER, "Ask the bot to deliver a paseo link; returns no bearer URL", (_NO, _CHAT)),
)

