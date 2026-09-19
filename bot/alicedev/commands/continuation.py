"""``/继续`` command: inject a follow-up turn into an existing session (§10).

Resolution order:
1. explicit ``/继续 <s_ref> <text>`` — the session ref is the first token;
2. otherwise a quoted bot message + text — the quoted ``platform_message_id`` is
   looked up in ``outbound`` and must map to exactly one session in *this* chat.

A closed session resumes automatically: paseo reloads the agent on the next
injection (§4), so the actor's ``inject`` transparently reactivates it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from alicedev.commands.registry import CommandSpec
from alicedev.paseo.session_actor import InjectBusyTimeout, SessionActorError

if TYPE_CHECKING:
    from alicedev.commands.context import CommandContext, Services
    from alicedev.commands.registry import CommandRegistry

_SESSION_RE = re.compile(r"^(s_[a-z2-7]{10})\b\s*(.*)$", re.S)

_SESSION_TOKEN = re.compile(r"s_[a-z2-7]{10}")


def _safe_error(exc: BaseException) -> str:
    """Keep actor diagnostics useful without exposing the internal session ref."""
    detail = _SESSION_TOKEN.sub("该会话", str(exc)).strip()
    return detail or "内部错误，请稍后再试。"


async def _resolve_session(ctx: "CommandContext") -> tuple[str | None, str, str | None]:
    """Return ``(session_ref, follow_up_text, error)``.

    ``session_ref`` is ``None`` when resolution fails; ``error`` then holds a
    user-facing message (Chinese)."""
    args = ctx.args.strip()

    match = _SESSION_RE.match(args)
    if match:
        return match.group(1), match.group(2).strip(), None

    quoted = ctx.quoted
    if quoted is not None and quoted.platform_message_id:
        row = await ctx.services.store.fetch_one(
            "SELECT session_ref, chat_key FROM outbound WHERE platform_message_id = ?",
            (quoted.platform_message_id,),
        )
        if row is None:
            return None, args, "找不到被引用消息对应的会话。"
        if row[1] != ctx.chat_key:
            return None, args, "该会话不属于本群。"
        return row[0], args, None

    return None, args, "用法：/继续 <会话号> <补充内容>，或引用 AI 的回复并补充内容。"


async def _handle_continue(ctx: "CommandContext") -> None:
    services = ctx.services
    session_ref, text, error = await _resolve_session(ctx)
    if session_ref is None:
        await ctx.reply_text(error or "用法：/继续 <会话号> <补充内容>")
        return
    if not text:
        await ctx.reply_text("请在 /继续 后补充要转达给 AI 的内容。")
        return

    row = await services.store.fetch_one(
        "SELECT chat_key, status FROM sessions WHERE session_ref = ?", (session_ref,)
    )
    if row is None:
        await ctx.reply_text("未找到这个会话。")
        return
    if row[0] != ctx.chat_key:
        await ctx.reply_text("该会话不属于本群。")
        return
    if row[1] == "archived":
        await ctx.reply_text("该会话已归档，无法继续。")
        return

    wrapped = (
        f"群友「{ctx.sender_name}」在同一需求下追加了内容：\n{text}\n\n"
        "请结合前文继续分析，并再次通过 chat_reply 回复群聊。"
    )
    try:
        await services.sessions.inject(
            session_ref=session_ref,
            text=wrapped,
            platform_message_id=ctx.event.message_obj.message_id,
            sender_key=ctx.user_key,
            select_current=True,
        )
    except InjectBusyTimeout:
        await ctx.reply_text("AI 仍在处理，稍后再试。")
        return
    except SessionActorError as exc:
        await ctx.reply_text(f"继续失败：{_safe_error(exc)}")
        return


def register(registry: "CommandRegistry", services: "Services") -> None:
    registry.register(
        CommandSpec(
            name="继续",
            aliases=("continue",),
            description="向已有会话补充内容并唤醒 AI 继续分析",
            admin_only=False,
            handler=_handle_continue,
        )
    )
