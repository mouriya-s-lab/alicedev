"""The in-process ``/升级bot`` lifecycle state machine.

The persisted ``upgrade_runs`` table intentionally exposes only the user-facing
states.  The conductor still models each internal hand-off explicitly so that
no operation can silently skip a daemon or verification step.
"""

from __future__ import annotations

from enum import Enum


class RunState(str, Enum):
    """Every state a single upgrade run can occupy.

    The internal states are deliberately distinct from the values persisted by
    :class:`UpgradeRunsRepo`: persistence is the message-layer projection,
    while this enum is the conductor's exhaustive control-flow model.
    """

    ACCEPTED = "accepted"
    MAIN_SYNC = "main_sync"
    WORKTREE = "worktree"
    IMPLEMENTATION = "impl_session"
    IMPL_SESSION = "impl_session"
    READ_COMMIT = "read_commit"
    TEST = "test_session"
    TEST_SESSION = "test_session"
    E2E = "e2e_convergence"
    E2E_CONVERGENCE = "e2e_convergence"
    RENDER = "render"
    CANDIDATE_READY = "candidate_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    DEPLOYING = "deploying"
    ACTIVE = "active"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"
    FAILED = "failed"
    MAIN_SYNC_FAILED = "main_sync_failed"


# Keep this relation explicit rather than deriving it from enum ordering.  A
# run may only move forward through the one-way conductor sequence; terminal
# states have no outgoing edges.
_ALLOWED_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.ACCEPTED: frozenset({RunState.MAIN_SYNC, RunState.FAILED}),
    RunState.MAIN_SYNC: frozenset({RunState.WORKTREE, RunState.MAIN_SYNC_FAILED, RunState.FAILED}),
    RunState.WORKTREE: frozenset({RunState.IMPLEMENTATION, RunState.FAILED}),
    RunState.IMPLEMENTATION: frozenset({RunState.IMPLEMENTATION, RunState.READ_COMMIT, RunState.FAILED}),
    RunState.READ_COMMIT: frozenset({RunState.IMPLEMENTATION, RunState.TEST, RunState.FAILED}),
    RunState.TEST: frozenset({RunState.IMPLEMENTATION, RunState.TEST, RunState.E2E, RunState.FAILED}),
    RunState.E2E: frozenset({RunState.IMPLEMENTATION, RunState.E2E, RunState.RENDER, RunState.FAILED}),
    RunState.RENDER: frozenset({RunState.CANDIDATE_READY, RunState.FAILED}),
    RunState.CANDIDATE_READY: frozenset({RunState.AWAITING_APPROVAL, RunState.FAILED}),
    RunState.AWAITING_APPROVAL: frozenset({RunState.DEPLOYING, RunState.FAILED}),
    RunState.DEPLOYING: frozenset({RunState.ACTIVE, RunState.ROLLED_BACK, RunState.ROLLBACK_FAILED, RunState.FAILED}),
    RunState.ACTIVE: frozenset(),
    RunState.ROLLED_BACK: frozenset(),
    RunState.ROLLBACK_FAILED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.MAIN_SYNC_FAILED: frozenset(),
}


class InvalidTransition(ValueError):
    """Raised when a conductor attempts to skip or reverse a lifecycle step."""


def allowed_transitions(state: RunState) -> frozenset[RunState]:
    """Return the immutable set of states reachable from ``state``."""

    match state:
        case RunState.ACCEPTED:
            return _ALLOWED_TRANSITIONS[RunState.ACCEPTED]
        case RunState.MAIN_SYNC:
            return _ALLOWED_TRANSITIONS[RunState.MAIN_SYNC]
        case RunState.WORKTREE:
            return _ALLOWED_TRANSITIONS[RunState.WORKTREE]
        case RunState.IMPLEMENTATION:
            return _ALLOWED_TRANSITIONS[RunState.IMPLEMENTATION]
        case RunState.READ_COMMIT:
            return _ALLOWED_TRANSITIONS[RunState.READ_COMMIT]
        case RunState.TEST:
            return _ALLOWED_TRANSITIONS[RunState.TEST]
        case RunState.E2E:
            return _ALLOWED_TRANSITIONS[RunState.E2E]
        case RunState.RENDER:
            return _ALLOWED_TRANSITIONS[RunState.RENDER]
        case RunState.CANDIDATE_READY:
            return _ALLOWED_TRANSITIONS[RunState.CANDIDATE_READY]
        case RunState.AWAITING_APPROVAL:
            return _ALLOWED_TRANSITIONS[RunState.AWAITING_APPROVAL]
        case RunState.DEPLOYING:
            return _ALLOWED_TRANSITIONS[RunState.DEPLOYING]
        case RunState.ACTIVE:
            return _ALLOWED_TRANSITIONS[RunState.ACTIVE]
        case RunState.ROLLED_BACK:
            return _ALLOWED_TRANSITIONS[RunState.ROLLED_BACK]
        case RunState.ROLLBACK_FAILED:
            return _ALLOWED_TRANSITIONS[RunState.ROLLBACK_FAILED]
        case RunState.FAILED:
            return _ALLOWED_TRANSITIONS[RunState.FAILED]
        case RunState.MAIN_SYNC_FAILED:
            return _ALLOWED_TRANSITIONS[RunState.MAIN_SYNC_FAILED]


def transition(state: RunState, next_state: RunState) -> RunState:
    """Validate and return one legal state transition."""

    if next_state not in allowed_transitions(state):
        raise InvalidTransition(f"cannot transition {state.value} -> {next_state.value}")
    return next_state


# These aliases keep the domain vocabulary convenient for callers without
# introducing a second enum or a second transition table.
UpgradeState = RunState
State = RunState

__all__ = [
    "InvalidTransition",
    "RunState",
    "State",
    "UpgradeState",
    "allowed_transitions",
    "transition",
]
