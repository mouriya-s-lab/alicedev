"""Contract B: the ``PaseoControl`` interface (ARCHITECTURE §4).

Operation-level contract is fixed; the transport (MCP ``POST /mcp/agents`` or the
daemon WebSocket ``/ws``) is chosen by the spike and selected at construction.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from enum import Enum


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
    """Identity of a paseo agent as seen by the bridge."""

    agent_id: str
    workspace_id: str | None = None
    server_id: str | None = None


class PaseoError(RuntimeError):
    """A paseo control-plane operation failed."""


class PaseoControl(abc.ABC):
    """Transport-agnostic control operations for one paseo daemon."""

    @abc.abstractmethod
    async def connect(self) -> None:
        """Establish the transport (idempotent)."""

    @abc.abstractmethod
    async def aclose(self) -> None:
        """Tear down the transport."""

    @abc.abstractmethod
    async def create(
        self,
        *,
        session_ref: str,
        provider: str,
        model: str,
        thinking: str,
        cwd: str,
        title: str,
        initial_prompt: str,
    ) -> AgentHandle:
        """Create an agent whose first turn is ``initial_prompt``.

        Spike verdict (§4): MCP ``create_agent`` requires ``initialPrompt``, so the
        bridge passes the ``/chat_ingress`` command as the initial prompt; the omp
        extension intercepts it before the model, keeping the msg id hidden.
        """

    @abc.abstractmethod
    async def find_by_label(self, session_ref: str) -> AgentHandle | None:
        """Recover an agent by its ``alicedev=<session_ref>`` label."""

    @abc.abstractmethod
    async def send(self, agent_id: str, text: str) -> None:
        """Send a user turn (the ingress command) to a live agent."""

    @abc.abstractmethod
    async def status(self, agent_id: str) -> AgentStatus:
        """Observe the agent's lifecycle/turn status."""

    @abc.abstractmethod
    async def close(self, agent_id: str) -> None:
        """Close the runtime while retaining a resumable record."""

    @abc.abstractmethod
    async def archive(self, agent_id: str) -> None:
        """Archive (soft delete) the agent."""


LABEL_KEY = "alicedev"
