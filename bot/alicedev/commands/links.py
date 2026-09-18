"""Placeholder for the share-link slice (owned by LinksGithub).

Empty until LinksGithub lands the admin-only ``/链接`` command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alicedev.commands.context import Services
    from alicedev.commands.registry import CommandRegistry


def register(registry: "CommandRegistry", services: "Services") -> None:
    return None
