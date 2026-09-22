"""The runtime's only door into the DSL slice (docs/slices-phase-a.md).

Every call into ``alicedev.dsl.*`` and ``alicedev.render.views`` goes through
here so the runtime depends on one small surface. Result shapes of the DSL
functions are normalized into the plain types below.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Union

from alicedev.domain import SessionView
from alicedev.dsl.model import (
    Action,
    AgentState,
    Command,
    FavoriteAction,
    GithubAction,
    HelpAction,
    HumanAction,
    ListAction,
    Registry,
    Scenario,
    SendAction,
    SessionArchiveAction,
    SessionRenameAction,
    SessionShowAction,
    SessionSwitchAction,
    ShareAction,
    StartAction,
    Tmpl,
)


@dataclass(frozen=True)
class Matched:
    command: Command
    session_no: int | None
    rest: str
    raw: Any  # the DSL slice's Invocation, passed back to parse_args


@dataclass(frozen=True)
class ArgsOk:
    values: Mapping[str, Any]


@dataclass(frozen=True)
class ArgsError:
    message: str


ArgsResult = Union[ArgsOk, ArgsError]


def load(root: Path) -> Registry:
    from alicedev.dsl.loader import load_registry

    return load_registry(root)


def render(tmpl: Tmpl, ctx: Mapping[str, Any]) -> str:
    from alicedev.dsl.render import render as _render

    return _render(tmpl, ctx)


def match(registry: Registry, text: str) -> Matched | None:
    from alicedev.dsl.invocation import match_command

    inv = match_command(registry, text)
    if inv is None:
        return None
    return Matched(
        command=inv.command,
        session_no=getattr(inv, "session_no", None),
        rest=str(getattr(inv, "rest", "") or ""),
        raw=inv,
    )


def parse_args(matched: Matched) -> ArgsResult:
    from alicedev.dsl.invocation import parse_args as _parse

    result = _parse(matched.command, matched.raw)
    message = getattr(result, "message", None)
    if isinstance(message, str) and not hasattr(result, "values"):
        return ArgsError(message)
    values = getattr(result, "values", result)
    if not isinstance(values, Mapping):
        return ArgsError(str(result))
    return ArgsOk(dict(values))


def reply_instructions(scenario: Scenario, state: AgentState) -> str:
    from alicedev.dsl.reply_instructions import reply_instructions as _ri

    return _ri(scenario, state)


_ACTION_NAMES: Mapping[type, str] = {
    StartAction: "start",
    GithubAction: "github",
    SendAction: "send",
    SessionShowAction: "session_show",
    SessionSwitchAction: "session_switch",
    SessionRenameAction: "session_rename",
    SessionArchiveAction: "session_archive",
    HumanAction: "human",
    ShareAction: "share",
    FavoriteAction: "favorite",
    ListAction: "list",
    HelpAction: "help",
}


def message_key(action: Action, result: str) -> str:
    """``messages.yaml`` key for an action result, e.g. ``start.created``."""
    return f"{_ACTION_NAMES[type(action)]}.{result}"


# --- card views (render/views.py, DSL slice) ---------------------------------


def help_card(registry: Registry, current: SessionView | None) -> tuple[str, dict[str, Any]]:
    from alicedev.render import views

    return views.help_card(registry, current)


def command_card(command: Command, scenario: Scenario | None) -> tuple[str, dict[str, Any]]:
    from alicedev.render import views

    return views.command_card(command, scenario)


def session_card(view: SessionView, scenario: Scenario | None) -> tuple[str, dict[str, Any]]:
    from alicedev.render import views

    return views.session_card(view, scenario)


def list_card(
    card: str, rows: list[Any], page: int, pages: int, *, archived: bool
) -> tuple[str, dict[str, Any]]:
    from alicedev.render import views

    match card:
        case "favorites_list":
            return views.favorites_list_card(rows, page, pages)
        case "requirements_list":
            return views.requirements_list_card(rows, page, pages)
        case _:
            return views.session_list_card(rows, page, pages, archived)
