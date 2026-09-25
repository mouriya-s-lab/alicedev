"""Typed model of the alicedev DSL (ARCHITECTURE §3).

Everything under ``templates/`` is parsed into these frozen types at the
boundary (``alicedev.dsl.loader``); the rest of the bot only sees these types.
Variants are closed: adding an action, argument type, route condition or state
kind means extending the unions here and every exhaustive match over them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping, Union


# --- source locations & errors ---------------------------------------------


@dataclass(frozen=True)
class SourceRef:
    """Where a DSL object came from, for error reports and cards."""

    path: Path
    line: int = 0


@dataclass(frozen=True)
class DslError:
    """One rejected file (reported in /v1/status ``dsl_errors``)."""

    path: str
    line: int
    message: str


# --- templates --------------------------------------------------------------


@dataclass(frozen=True)
class Tmpl:
    """A Jinja2 source string (rendered with StrictUndefined)."""

    source: str


# --- common enums -----------------------------------------------------------


class CommandType(str, Enum):
    AI = "ai"
    PROGRAM = "program"


class Permission(str, Enum):
    ALL = "all"
    OWNER = "owner"  # session creator or admin
    ADMIN = "admin"


class ArgType(str, Enum):
    SESSION = "session"
    TEXT = "text"
    WORD = "word"
    INT = "int"
    PAGE = "page"
    GITHUB_REF = "github_ref"
    MENTIONS = "mentions"
    QUOTED = "quoted"


class ListSource(str, Enum):
    SESSIONS = "sessions"
    FAVORITES = "favorites"


class RouteWhen(str, Enum):
    GITHUB_LINK = "github_link"
    ADDRESSED = "addressed"


# --- usage ------------------------------------------------------------------


@dataclass(frozen=True)
class UsageParam:
    """One parameter parsed from the ``usage`` line (``[x]`` optional, ``<x>`` required)."""

    name: str
    type: ArgType
    optional: bool


# --- actions (closed set, ARCHITECTURE §3.2) --------------------------------
# An ``ArgName`` names a command argument; ``None`` means "use the default
# resolution" (session: quoted message's session, then current session).

ArgName = str


@dataclass(frozen=True)
class StartAction:
    scenario: str
    text: Tmpl


@dataclass(frozen=True)
class GithubAction:
    ref: Tmpl
    scenarios: Mapping[str, str]  # {"issue": <scenario>, "pr": <scenario>}


@dataclass(frozen=True)
class SendAction:
    session: ArgName | None
    text: Tmpl


@dataclass(frozen=True)
class SessionShowAction:
    session: ArgName | None


@dataclass(frozen=True)
class SessionSwitchAction:
    session: ArgName | None


@dataclass(frozen=True)
class SessionRenameAction:
    session: ArgName | None
    name: Tmpl


@dataclass(frozen=True)
class SessionArchiveAction:
    session: ArgName | None


@dataclass(frozen=True)
class HumanAction:
    session: ArgName | None
    command: str


@dataclass(frozen=True)
class ShareAction:
    session: ArgName | None
    to: ArgName | None  # mentions arg; None = the sender


@dataclass(frozen=True)
class FavoriteAction:
    quoted: ArgName


@dataclass(frozen=True)
class ListAction:
    source: ListSource
    card: str
    page: ArgName | None
    scenario: str | None = None
    archived: bool = False


@dataclass(frozen=True)
class HelpAction:
    command: ArgName | None


@dataclass(frozen=True)
class ChatsAction:
    """群列表: chats with sessions plus ``allowed_chats`` (§3.2)."""


@dataclass(frozen=True)
class StatusAction:
    """Bot status card, same source as ``/v1/status`` (§3.2)."""


Action = Union[
    StartAction,
    GithubAction,
    SendAction,
    SessionShowAction,
    SessionSwitchAction,
    SessionRenameAction,
    SessionArchiveAction,
    HumanAction,
    ShareAction,
    FavoriteAction,
    ListAction,
    ChatsAction,
    StatusAction,
    HelpAction,
]

AI_ACTIONS: tuple[type, ...] = (StartAction, GithubAction, SendAction)

# Actions an agent may invoke through ``alicedev run`` (§3.2 "agent 可调用").
AGENT_ACTIONS: tuple[type, ...] = (
    StartAction,
    GithubAction,
    SessionShowAction,
    SessionRenameAction,
    SessionArchiveAction,
    FavoriteAction,
    ListAction,
    ChatsAction,
    StatusAction,
)

# Fixed result keys per action (``say`` may only override these).
RESULT_KEYS: Mapping[type, tuple[str, ...]] = {
    StartAction: ("created", "queued", "failed", "sent", "busy"),
    GithubAction: ("created", "fetch_failed", "failed"),
    SendAction: ("ok", "not_found", "not_conversational", "busy"),
    SessionShowAction: ("not_found",),
    SessionSwitchAction: ("ok", "not_found"),
    SessionRenameAction: ("ok", "not_found"),
    SessionArchiveAction: ("ok", "not_found"),
    HumanAction: ("ok", "not_found", "not_allowed"),
    ShareAction: ("ok", "not_found", "not_ready"),
    FavoriteAction: ("ok", "image_failed"),
    ListAction: ("empty",),
    ChatsAction: (),
    StatusAction: (),
    HelpAction: ("not_found",),
}


# --- commands ----------------------------------------------------------------


@dataclass(frozen=True)
class Command:
    name: str
    type: CommandType
    summary: str
    usage: str
    params: tuple[UsageParam, ...]
    examples: tuple[str, ...]
    permission: Permission
    do: Action
    say: Mapping[str, Tmpl]
    aliases: tuple[str, ...] = ()
    subcommands: Mapping[str, "Command"] = field(default_factory=dict)
    source: SourceRef | None = None


# --- routes ------------------------------------------------------------------


@dataclass(frozen=True)
class Route:
    when: RouteWhen
    do: Action
    say: Mapping[str, Tmpl]


# --- scenarios ----------------------------------------------------------------


@dataclass(frozen=True)
class ReplySpec:
    kinds: tuple[str, ...]
    image_templates: tuple[str, ...] = ()
    text_templates: tuple[str, ...] = ()
    stickers: tuple[str, ...] = ()
    max_text_chars: int = 600

    def allows(self, kind: str) -> bool:
        return kind in self.kinds


@dataclass(frozen=True)
class CwdWorkdir:
    path: str


@dataclass(frozen=True)
class RepoWorkdir:
    fixed_main: str
    base: str


Workdir = Union[CwdWorkdir, RepoWorkdir]


class Audience(str, Enum):
    ALL = "all"
    ADMIN = "admin"  # admin-only input; may list admin commands and act on other chats (§3.4)


@dataclass(frozen=True)
class AgentState:
    name: str
    prompt: Tmpl
    reply: ReplySpec | None  # None = internal state; set = conversational
    next: tuple[str, ...] = ()
    data: tuple[str, ...] = ()
    share: bool = False
    agent_commands: tuple[str, ...] = ()  # command paths ("会话列表 全部") callable via alicedev run

    @property
    def conversational(self) -> bool:
        return self.reply is not None


@dataclass(frozen=True)
class HumanState:
    name: str
    commands: Mapping[str, str]  # human command -> target state
    reply: ReplySpec | None = None  # spec for the AI message entering it
    data: tuple[str, ...] = ()
    share: bool = False


@dataclass(frozen=True)
class TerminalState:
    name: str
    reply: ReplySpec | None = None
    data: tuple[str, ...] = ()
    share: bool = False


State = Union[AgentState, HumanState, TerminalState]

# Built-in states every scenario has (ARCHITECTURE §3.4).
QUEUED = "queued"
MAIN_SYNC_FAILED = "main_sync_failed"
FAILED = "failed"
ARCHIVED = "archived"
BUILTIN_STATES: tuple[str, ...] = (QUEUED, MAIN_SYNC_FAILED, FAILED, ARCHIVED)


def is_visible(state: State) -> bool:
    match state:
        case AgentState():
            return state.conversational
        case HumanState() | TerminalState():
            return True


@dataclass(frozen=True)
class Scenario:
    name: str
    title: Tmpl
    description: str
    provider: str
    workdir: Workdir
    initial: str
    states: Mapping[str, State]
    exclusive: bool = False
    one_per_chat: bool = False
    audience: Audience = Audience.ALL
    source: SourceRef | None = None

    def state(self, name: str) -> State:
        """Scenario state or built-in terminal; KeyError if unknown."""
        if name in self.states:
            return self.states[name]
        if name == MAIN_SYNC_FAILED:
            return TerminalState(name=name, share=True)
        if name in (FAILED, ARCHIVED):
            return TerminalState(name=name)
        raise KeyError(name)


# --- registry -----------------------------------------------------------------


@dataclass(frozen=True)
class Registry:
    commands: Mapping[str, Command]  # name and aliases -> command
    routes: tuple[Route, ...]
    scenarios: Mapping[str, Scenario]
    messages: Mapping[str, Tmpl]  # "<action>.<result>" and system keys
    errors: tuple[DslError, ...]

    def command_list(self) -> list[Command]:
        """Unique commands (aliases collapsed), in name order."""
        seen: dict[str, Command] = {}
        for cmd in self.commands.values():
            seen.setdefault(cmd.name, cmd)
        return [seen[k] for k in sorted(seen)]

    def command_at(self, path: str) -> Command | None:
        """The command (or subcommand) at a canonical path like ``会话列表 全部``; aliases don't count."""
        head, *rest = path.split()
        cmd = self.commands.get(head)
        if cmd is None or cmd.name != head:
            return None
        for word in rest:
            sub = cmd.subcommands.get(word)
            if sub is None or sub.name != word:
                return None
            cmd = sub
        return cmd
