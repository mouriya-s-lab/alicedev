"""Parse the independent HTTP tool envelope into closed invocation variants."""

from __future__ import annotations

from typing import assert_never

from alicedev.agent_tools.model import (
    ChatsList, FavoriteAdd, FavoritesList, GithubAnalysisStart, InvestigationStart,
    RequirementAdd, RequirementsList, SessionArchive, SessionGet,
    SessionLinkDeliver, SessionList, SessionRename, StatusGet, TOOL_SPECS,
    ToolError, ToolFailure, ToolInvocation, ToolName, ToolRequest,
)

from alicedev.domain import JsonValue

class _InvalidPayload(ValueError):
    """Expected JSON-boundary rejection; never crosses the parser."""


def _string(value: JsonValue) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _InvalidPayload
    return value


class _Arguments:
    def __init__(self, name: ToolName, value: JsonValue) -> None:
        if not isinstance(value, dict):
            raise _InvalidPayload
        spec = next(spec for spec in TOOL_SPECS if spec.name == name)
        allowed = {parameter.name for parameter in spec.parameters}
        required = {parameter.name for parameter in spec.parameters if parameter.required}
        if not required.issubset(value) or not value.keys() <= allowed:
            raise _InvalidPayload
        self._value = value

    def string(self, key: str) -> str:
        return _string(self._value[key])

    def optional_string(self, key: str) -> str | None:
        return _string(self._value[key]) if key in self._value else None

    def positive_int(self, key: str, default: int | None = None) -> int:
        if key not in self._value and default is not None:
            return default
        value = self._value[key]
        if type(value) is not int or value < 1:
            raise _InvalidPayload
        return value

    def boolean(self, key: str, default: bool) -> bool:
        if key not in self._value:
            return default
        value = self._value[key]
        if not isinstance(value, bool):
            raise _InvalidPayload
        return value


def _invocation(name: ToolName, args: _Arguments) -> ToolInvocation:
    match name:
        case ToolName.SESSION_GET:
            return SessionGet(args.positive_int("no"), args.optional_string("chat"))
        case ToolName.SESSION_LIST:
            return SessionList(args.positive_int("page", 1), args.boolean("include_ended", False), args.optional_string("chat"))
        case ToolName.CHATS_LIST:
            return ChatsList()
        case ToolName.STATUS_GET:
            return StatusGet()
        case ToolName.REQUIREMENTS_LIST:
            return RequirementsList(args.positive_int("page", 1), args.optional_string("chat"))
        case ToolName.FAVORITES_LIST:
            return FavoritesList(args.positive_int("page", 1), args.optional_string("chat"))
        case ToolName.REQUIREMENT_ADD:
            return RequirementAdd(args.string("text"), args.optional_string("chat"), args.optional_string("quote"))
        case ToolName.FAVORITE_ADD:
            return FavoriteAdd(args.string("quote"), args.optional_string("chat"))
        case ToolName.INVESTIGATION_START:
            return InvestigationStart(args.string("text"), args.optional_string("chat"))
        case ToolName.GITHUB_ANALYSIS_START:
            return GithubAnalysisStart(args.string("url"), args.optional_string("chat"))
        case ToolName.SESSION_ARCHIVE:
            return SessionArchive(args.positive_int("no"), args.optional_string("chat"))
        case ToolName.SESSION_RENAME:
            return SessionRename(args.positive_int("no"), args.string("name"), args.optional_string("chat"))
        case ToolName.SESSION_LINK_DELIVER:
            return SessionLinkDeliver(args.positive_int("no"), args.optional_string("chat"))
    assert_never(name)


def parse_tool_request(name: str, body: JsonValue) -> ToolRequest | ToolFailure:
    try:
        tool_name = ToolName(name)
    except ValueError:
        return ToolFailure(ToolError.UNKNOWN_TOOL, 400)
    try:
        if not isinstance(body, dict) or body.keys() != {"agent", "call_id", "args"}:
            raise _InvalidPayload
        agent_ref = _string(body["agent"])
        call_id = _string(body["call_id"])
        invocation = _invocation(tool_name, _Arguments(tool_name, body["args"]))
        return ToolRequest(agent_ref, call_id, invocation)
    except _InvalidPayload:
        return ToolFailure(ToolError.INVALID_PAYLOAD, 400)
