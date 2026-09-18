"""Command registry — the single source of truth for slash-command routing.

Each slice registers its commands through a ``register(registry, services)``
function that ``bot/main.py`` imports and calls once during ``initialize()``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from alicedev.commands.context import CommandContext

CommandHandler = Callable[["CommandContext"], Awaitable[None]]


@dataclass(frozen=True)
class CommandSpec:
    """A registered slash command."""

    name: str
    aliases: tuple[str, ...]
    description: str
    admin_only: bool
    handler: CommandHandler


class CommandRegistry:
    """Resolve a command token (without slash) to its :class:`CommandSpec`."""

    def __init__(self) -> None:
        self._by_name: dict[str, CommandSpec] = {}
        self._specs: list[CommandSpec] = []

    def register(self, spec: CommandSpec) -> None:
        if spec.name in self._by_name and self._by_name[spec.name] is not spec:
            raise ValueError(f"command name already registered: {spec.name!r}")
        self._by_name[spec.name] = spec
        for alias in spec.aliases:
            existing = self._by_name.get(alias)
            if existing is not None and existing is not spec:
                raise ValueError(
                    f"command alias {alias!r} already bound to {existing.name!r}"
                )
            self._by_name[alias] = spec
        if spec not in self._specs:
            self._specs.append(spec)

    def resolve(self, name: str) -> CommandSpec | None:
        return self._by_name.get(name)

    def all(self) -> list[CommandSpec]:
        return list(self._specs)
