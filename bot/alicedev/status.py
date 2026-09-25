"""Shared bot-status snapshot for the API and status card."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from alicedev.dsl.model import Registry
    from alicedev.outbox.service import Outbox
    from alicedev.store.agents_repo import AgentsRepo
    from alicedev.store.sessions_repo import SessionsRepo


@dataclass(frozen=True)
class BotStatus:
    generation: int
    revision: str
    uptime_s: int
    platforms: list[str]
    commands: list[str]
    scenarios: list[str]
    dsl_errors: list[dict[str, Any]]
    sessions: dict[str, int]
    agents: dict[str, int]
    outbox_pending: int


def status_json(status: BotStatus) -> dict[str, Any]:
    """Serialize the shared status snapshot using the /v1/status field names."""
    return {
        "generation": status.generation,
        "revision": status.revision,
        "uptime_s": status.uptime_s,
        "platforms": status.platforms,
        "commands": status.commands,
        "scenarios": status.scenarios,
        "dsl_errors": status.dsl_errors,
        "sessions": status.sessions,
        "agents": status.agents,
        "outbox_pending": status.outbox_pending,
    }


async def collect_status(
    registry: Registry,
    sessions: SessionsRepo,
    agents: AgentsRepo,
    outbox: Outbox,
    platforms_fn: Callable[[], list[str]],
    generation: int,
    revision: str,
    started_at: float,
    ended_states: tuple[str, ...],
) -> BotStatus:
    """Collect a status snapshot from the live registry and repositories."""
    session_counts = await sessions.counts(ended_states)
    agent_counts = await agents.counts()
    return BotStatus(
        generation=generation,
        revision=revision,
        uptime_s=int(time.monotonic() - started_at),
        platforms=platforms_fn(),
        commands=[command.name for command in registry.command_list()],
        scenarios=sorted(registry.scenarios),
        dsl_errors=[asdict(error) for error in registry.errors],
        sessions=session_counts,
        agents={"active": agent_counts.get("active", 0), "closed": agent_counts.get("closed", 0)},
        outbox_pending=await outbox.repo.pending_count(),
    )
