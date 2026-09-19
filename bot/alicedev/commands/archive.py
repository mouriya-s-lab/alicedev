"""``/归档`` — archive a paseo session for administrators."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from alicedev.commands.registry import CommandSpec

if TYPE_CHECKING:
    from alicedev.commands.context import CommandContext, Services
    from alicedev.commands.registry import CommandRegistry


_SESSION_REF = re.compile(r"^s_[a-z2-7]{10}$")


async def _handle_archive(ctx: "CommandContext") -> None:
    session_ref = ctx.args.strip()
    if not _SESSION_REF.fullmatch(session_ref):
        await ctx.reply_text("用法：/归档 <s_ref>")
        return

    row = await ctx.services.store.fetch_one(
        "SELECT session_ref, name FROM sessions "
        "WHERE session_ref = ? AND chat_key = ?",
        (session_ref, ctx.chat_key),
    )
    if row is None:
        await ctx.reply_text("未找到这个会话。")
        return

    await ctx.services.sessions.archive(session_ref)
    await ctx.reply_text(f"已归档会话：「{row[1]}」")


def register(registry: "CommandRegistry", services: "Services") -> None:
    registry.register(
        CommandSpec(
            name="归档",
            aliases=("archive",),
            description="归档一个 paseo 会话",
            admin_only=True,
            handler=_handle_archive,
        )
    )
