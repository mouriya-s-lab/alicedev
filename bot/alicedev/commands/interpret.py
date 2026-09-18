"""Placeholder for the interpret slice (owned by LinksGithub).

Empty until LinksGithub lands ``/解读`` (and the ``/归档`` admin command). The
bare-GitHub-URL auto-router lives in ``dispatch.py`` and needs no registration
here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alicedev.commands.context import Services
    from alicedev.commands.registry import CommandRegistry


def register(registry: "CommandRegistry", services: "Services") -> None:
    return None
