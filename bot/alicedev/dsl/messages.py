"""Keys of ``templates/messages.yaml`` (default replies, ARCHITECTURE §3.1)."""

from __future__ import annotations

from alicedev.dsl.model import (
    RESULT_KEYS,
    ChatsAction,
    FavoriteAction,
    GithubAction,
    HelpAction,
    HumanAction,
    ListAction,
    SendAction,
    SessionArchiveAction,
    SessionRenameAction,
    SessionShowAction,
    SessionSwitchAction,
    ShareAction,
    StartAction,
    StatusAction,
)

ACTION_NAMES: dict[type, str] = {
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
    ChatsAction: "chats",
    StatusAction: "status",
    HelpAction: "help",
}

# System keys the runtime uses outside of action results.
SYSTEM_KEYS: tuple[str, ...] = (
    "unknown_command",  # vars: command
    "usage_error",  # vars: usage, error
    "internal_error",  # vars: reason
)

# Optional: text posted when a session enters a state *not* via an AI reply
# (scheduler or human command), keyed ``state.<name>``; falls back to
# ``state.default``. Vars: session, reason, data, link.
STATE_DEFAULT_KEY = "state.default"


def message_key(action_type: type, result: str) -> str:
    return f"{ACTION_NAMES[action_type]}.{result}"


def state_key(state: str) -> str:
    return f"state.{state}"


def required_keys() -> tuple[str, ...]:
    keys = [message_key(t, r) for t, results in RESULT_KEYS.items() for r in results]
    return tuple(keys) + SYSTEM_KEYS + (STATE_DEFAULT_KEY,)
