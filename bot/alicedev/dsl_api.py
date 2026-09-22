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
from alicedev.dsl.model import Action, AgentState, Command, Registry, Scenario, Tmpl


@dataclass(frozen=True)
class Matched:
    command: Command
    path: tuple[str, ...]  # command words, e.g. ("会话列表", "全部")
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
        path=inv.path,
        session_no=getattr(inv, "session_no", None),
        rest=str(getattr(inv, "rest", "") or ""),
        raw=inv,
    )


def parse_args(matched: Matched) -> ArgsResult:
    from alicedev.dsl.invocation import ArgError, parse_args as _parse

    result = _parse(matched.command, matched.raw)
    if isinstance(result, ArgError):
        return ArgsError(result.message)
    return ArgsOk(dict(result.values))


def reply_instructions(scenario: Scenario, state: AgentState) -> str:
    from alicedev.dsl.reply_instructions import reply_instructions as _ri

    return _ri(scenario, state)


def message_key(action: Action, result: str) -> str:
    """``messages.yaml`` key for an action result, e.g. ``start.created``."""
    from alicedev.dsl.messages import message_key as _key

    return _key(type(action), result)


# --- card views (render/views.py, DSL slice) ---------------------------------


def help_card(
    registry: Registry, current: SessionView | None, scenario: Scenario | None
) -> tuple[str, dict[str, Any]]:
    from alicedev.render import views

    return views.help_card(registry, current, scenario)


def command_card(registry: Registry, command: Command) -> tuple[str, dict[str, Any]]:
    from alicedev.render import views

    return views.command_card(command, views.command_scenario(registry, command))


def session_card(view: SessionView, scenario: Scenario | None) -> tuple[str, dict[str, Any]]:
    from alicedev.render import views

    return views.session_card(view, scenario)


def session_list_card(
    card: str,
    rows: list[SessionView],
    page: int,
    pages: int,
    *,
    archived: bool,
    command: str,
    scenarios: Mapping[str, Scenario],
) -> tuple[str, dict[str, Any]]:
    from alicedev.render import views

    builder = views.SESSION_LIST_CARDS.get(card, views.SESSION_LIST_CARDS["session_list"])
    return builder(rows, page, pages, archived=archived, command=command, scenarios=scenarios)


def favorites_list_card(
    rows: list[Any], page: int, pages: int, *, command: str
) -> tuple[str, dict[str, Any]]:
    from alicedev.render import views

    return views.favorites_list_card(rows, page, pages, command=command)
