"""bot → paseo control plane contract (ARCHITECTURE §4).

The only transport is the paseo CLI inside the paseo container
(:mod:`alicedev.paseo.cli`); this module holds the transport-free types.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from enum import Enum

LABEL_KEY = "alicedev"


class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PERMISSION = "permission"
    CLOSED = "closed"
    ERROR = "error"
    UNKNOWN = "unknown"

    @property
    def busy(self) -> bool:
        return self in (AgentStatus.RUNNING, AgentStatus.PERMISSION)


@dataclass(frozen=True)
class AgentHandle:
    agent_id: str
    workspace_id: str | None
    server_id: str | None



@dataclass(frozen=True)
class LiveAgent:
    """A global-visible paseo agent with non-closed status; not an exhaustive PID inventory."""

    agent_id: str
    status: AgentStatus

@dataclass(frozen=True)
class WorkspaceRef:
    workspace_id: str
    cwd: str


@dataclass(frozen=True)
class GitResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class PaseoError(RuntimeError):
    """A paseo control-plane operation failed."""


class PaseoControl(abc.ABC):
    """Operations the bot performs on the paseo daemon (all via paseo CLI)."""

    @abc.abstractmethod
    async def create(
        self,
        *,
        agent_ref: str,
        provider: str,
        cwd: str,
        title: str,
        initial_prompt: str,
        workspace_id: str,
    ) -> AgentHandle:
        """Start an agent (labelled ``alicedev=<agent_ref>``) with its first turn."""

    @abc.abstractmethod
    async def find_by_label(self, agent_ref: str) -> AgentHandle | None:
        """Recover an agent created with ``alicedev=<agent_ref>``."""

    @abc.abstractmethod
    async def send(self, agent_id: str, text: str) -> None: ...

    @abc.abstractmethod
    async def status(self, agent_id: str) -> AgentStatus: ...

    @abc.abstractmethod
    async def live_agents(self) -> list[LiveAgent]:
        """Agents holding a resident runtime right now; raises ``PaseoError`` if unknown."""

    @abc.abstractmethod
    async def park(self, agent_id: str) -> None:
        """Release the idle agent's runtime; a later ``send`` wakes it with its conversation."""

    @abc.abstractmethod
    async def archive(self, agent_id: str) -> None: ...

    @abc.abstractmethod
    async def is_archived(self, agent_id: str) -> bool:
        """Read the explicit archive fact from inspect; closed status is not enough."""

    @abc.abstractmethod
    async def all_agent_ids(self) -> list[str]:
        """Global visible inventory including archived records; not a storage/lifecycle proof."""

    @abc.abstractmethod
    async def workspace_local(self, path: str, title: str) -> WorkspaceRef:
        """Register a non-isolated workspace on ``path`` (cwd scenarios, fixed-main)."""

    @abc.abstractmethod
    async def worktree_create(self, *, repo: str, base_ref: str, slug: str) -> WorkspaceRef: ...

    @abc.abstractmethod
    async def workspace_archive(self, workspace_id: str) -> None: ...

    @abc.abstractmethod
    async def server_id(self) -> str | None: ...

    @abc.abstractmethod
    async def git(self, repo: str, *args: str) -> GitResult:
        """Run ``git -C <repo> <args>`` inside the paseo container as the paseo user."""
