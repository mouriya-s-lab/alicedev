"""Placeholder for the list slice (owned by CardsFavorites).

Empty until CardsFavorites lands ``/收藏夹`` / ``/需求列表`` paginated card lists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alicedev.commands.context import Services
    from alicedev.commands.registry import CommandRegistry


def register(registry: "CommandRegistry", services: "Services") -> None:
    return None
