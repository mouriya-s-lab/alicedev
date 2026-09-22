"""alicedev DSL: typed model, loader/validator, invocation parsing, rendering (ARCHITECTURE §3)."""

from alicedev.dsl.invocation import (
    ArgError,
    ArgValue,
    FromEvent,
    Invocation,
    ParsedArgs,
    SessionArg,
    match_command,
    parse_args,
)
from alicedev.dsl.loader import load_registry
from alicedev.dsl.messages import message_key, state_key
from alicedev.dsl.render import context, render
from alicedev.dsl.reply_instructions import reply_instructions

__all__ = [
    "ArgError",
    "ArgValue",
    "FromEvent",
    "Invocation",
    "ParsedArgs",
    "SessionArg",
    "context",
    "load_registry",
    "match_command",
    "message_key",
    "parse_args",
    "render",
    "reply_instructions",
    "state_key",
]
