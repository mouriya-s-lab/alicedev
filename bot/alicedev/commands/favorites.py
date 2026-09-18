"""Placeholder for the favorites slice (owned by CardsFavorites).

BridgeCore ships this as an empty module so ``bot/main.py`` can import and call
``register`` before that slice lands. CardsFavorites replaces the body with the
real ``/收藏`` / ``/收藏夹`` handlers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alicedev.commands.context import Services
    from alicedev.commands.registry import CommandRegistry


def register(registry: "CommandRegistry", services: "Services") -> None:
    """No-op until CardsFavorites lands the favorites commands."""
    return None
