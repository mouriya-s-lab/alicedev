"""Match a chat message to a command and parse its arguments (ARCHITECTURE §3.2, §9).

``match_command`` resolves name/alias → optional subcommand word → optional
session marker ``%n`` / ``％n`` (only for commands that declare a session
parameter). ``parse_args`` then fills the remaining typed parameters from the
rest of the text. Event-provided parameters (mentions, quoted) are returned as
:class:`FromEvent` markers; the runtime fills them from the platform event.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Union

from alicedev.dsl.model import ArgType, Command, Registry

_PREFIXES = ("/", "／")
_NAME = re.compile(r"([^\s%％]+)(.*)", re.S)
_SUBWORD = re.compile(r"\s+([^\s%％]+)(.*)", re.S)
_MARK = re.compile(r"\s*[%％]\s*(\d+)", re.S)
_MENTION_TOKEN = re.compile(r"(?<!\S)[@＠]\S+")


@dataclass(frozen=True)
class Invocation:
    command: Command  # the leaf command (subcommand when one matched)
    path: tuple[str, ...]  # (name,) or (name, subcommand)
    session_no: int | None  # from %n / ％n
    rest: str  # remaining text after name, subcommand word and marker


def match_command(reg: Registry, text: str) -> Invocation | None:
    """Return the invocation if ``text`` is a known slash command, else None."""

    stripped = (text or "").strip()
    if not stripped.startswith(_PREFIXES):
        return None
    head = _NAME.match(stripped[1:].lstrip())
    if head is None:
        return None
    name, rest = head.group(1), head.group(2)
    command = reg.commands.get(name)
    if command is None:
        return None
    path: tuple[str, ...] = (command.name,)
    if command.subcommands:
        sub = _SUBWORD.match(rest)
        if sub is not None and sub.group(1) in command.subcommands:
            command = command.subcommands[sub.group(1)]
            path = path + (sub.group(1),)
            rest = sub.group(2)
    session_no: int | None = None
    if any(p.type is ArgType.SESSION for p in command.params):
        mark = _MARK.match(rest)
        if mark is not None:
            session_no = int(mark.group(1))
            rest = rest[mark.end() :]
    return Invocation(command=command, path=path, session_no=session_no, rest=rest.strip())


# --- argument values ----------------------------------------------------------


@dataclass(frozen=True)
class SessionArg:
    """``no`` is the explicit %n, or None → runtime default (quoted, then current)."""

    no: int | None


class FromEvent(str, Enum):
    MENTIONS = "mentions"
    QUOTED = "quoted"


ArgValue = Union[str, int, SessionArg, FromEvent, None]


@dataclass(frozen=True)
class ParsedArgs:
    values: Mapping[str, ArgValue]


@dataclass(frozen=True)
class ArgError:
    message: str


def parse_args(cmd: Command, inv: Invocation) -> ParsedArgs | ArgError:
    """Fill ``cmd.params`` from the invocation (see module docstring)."""

    values: dict[str, ArgValue] = {}
    rest = _MENTION_TOKEN.sub(" ", inv.rest) if _has(cmd, ArgType.MENTIONS) else inv.rest
    rest = rest.strip()
    for param in cmd.params:
        match param.type:
            case ArgType.SESSION:
                if inv.session_no is None and not param.optional:
                    return ArgError(f"需要用 %n 指定{param.name}")
                values[param.name] = SessionArg(no=inv.session_no)
            case ArgType.MENTIONS:
                values[param.name] = FromEvent.MENTIONS
            case ArgType.QUOTED:
                values[param.name] = FromEvent.QUOTED
            case ArgType.TEXT:
                if not rest and not param.optional:
                    return ArgError(f"缺少{param.name}")
                values[param.name] = rest or None
                rest = ""
            case ArgType.WORD | ArgType.GITHUB_REF:
                token, rest = _next_token(rest)
                if token is None and not param.optional:
                    return ArgError(f"缺少{param.name}")
                values[param.name] = token
            case ArgType.INT | ArgType.PAGE:
                token, rest = _next_token(rest)
                if token is None:
                    if not param.optional:
                        return ArgError(f"缺少{param.name}")
                    values[param.name] = 1 if param.type is ArgType.PAGE else None
                    continue
                try:
                    number = int(token)
                except ValueError:
                    return ArgError(f"{param.name}必须是数字：{token}")
                if param.type is ArgType.PAGE and number < 1:
                    return ArgError(f"{param.name}必须大于 0")
                values[param.name] = number
    if rest:
        return ArgError(f"多余的内容：{rest}")
    return ParsedArgs(values=values)


def _has(cmd: Command, arg_type: ArgType) -> bool:
    return any(p.type is arg_type for p in cmd.params)


def _next_token(rest: str) -> tuple[str | None, str]:
    parts = rest.split(None, 1)
    if not parts:
        return None, ""
    return parts[0], (parts[1] if len(parts) > 1 else "")
