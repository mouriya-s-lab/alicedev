"""``/alicedev`` — show the curated alicedev quick-start card."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from alicedev.commands.registry import CommandSpec
from alicedev.store.sessions_repo import SessionStatus

if TYPE_CHECKING:
    from alicedev.commands.context import CommandContext, Services
    from alicedev.commands.registry import CommandRegistry
    from alicedev.store.sessions_repo import SessionRecord

_LOG = logging.getLogger("alicedev.commands.help")

_STATUS_LABELS = {
    SessionStatus.CREATING: "创建中",
    SessionStatus.ACTIVE: "进行中",
    SessionStatus.CLOSED: "已关闭",
    SessionStatus.FAILED: "失败",
    SessionStatus.ARCHIVED: "已归档",
}


def _entry(
    registry: "CommandRegistry", command: str, fallback: str
) -> dict[str, str]:
    """Build one curated entry, including registry-owned admin visibility."""
    spec = registry.resolve(command)
    description = spec.description.strip() if spec is not None else ""
    display_command = f"/{command}"
    if spec is not None and spec.admin_only:
        display_command += " [管理员]"
    return {
        "command": display_command,
        "description": description or fallback,
    }


def _session_display(current: "SessionRecord | None") -> tuple[str, str]:
    if current is None:
        return "未选择", ""
    name = current.name.strip()
    if not name:
        return "未选择", ""
    return name, _STATUS_LABELS[current.status]


def _fallback_text(
    current_name: str, current_status: str, registry: "CommandRegistry"
) -> str:
    status = f"（{current_status}）" if current_status else ""
    session_commands = "；".join(
        _entry(registry, command, "").get("command", f"/{command}")
        for command in ("继续", "链接", "归档")
    )
    return (
        f"alicedev 帮助｜当前会话：{current_name}{status}\n"
        "常用：@bot 普通文本；@bot 后仅完整 GitHub Issue/PR URL；"
        "/解读 <GitHub URL 或 #n>；/需求；/帮我调查。\n"
        f"会话：{session_commands}。记录：/需求列表；/收藏夹；/收藏。\n"
        "规则：URL 加任何文字按普通对话注入当前会话；成功 /继续 不额外回复；"
        "无当前会话时不会自动改用最近会话。"
    )


async def _handle_help(ctx: "CommandContext", registry: "CommandRegistry") -> None:
    current = await ctx.services.sessions.current_for_chat(ctx.chat_key)
    current_name, current_status = _session_display(current)
    fields: dict[str, object] = {
        "title": "使用帮助",
        "eyebrow": "alicedev",
        "subtitle": "把需求、调查和链接交给当前会话",
        "current_name": current_name,
        "current_status": current_status,
        "quick_entries": (
            {
                "command": "@bot 普通文本",
                "description": "把整段文字注入当前会话",
            },
            {
                "command": "@bot 纯 GitHub Issue/PR URL",
                "description": "仅完整 URL 会新建 GitHub 解读会话",
            },
            _entry(registry, "解读", "预取并解读 GitHub Issue 或 Pull Request"),
            _entry(registry, "需求", "记录需求并交给 AI 分析"),
            _entry(registry, "帮我调查", "调查一个问题并直接回复"),
        ),
        "session_actions": (
            _entry(registry, "继续", "向已有会话补充内容"),
            _entry(registry, "链接", "分享当前会话工作区"),
            _entry(registry, "归档", "归档一个会话"),
        ),
        "record_actions": (
            _entry(registry, "需求列表", "查看当前聊天的需求列表"),
            _entry(registry, "收藏夹", "查看当前聊天的收藏夹"),
            _entry(registry, "收藏", "收藏一条引用消息（可包含图片）"),
        ),
        "rules": (
            "GitHub URL 后附加任何文字时，整段都按普通对话注入当前会话。",
            "成功执行 /继续 后不会发送额外的确认或处理中提示。",
            "没有当前会话时会明确提示，不会自动改用最近会话。",
        ),
        "footer": "需要帮助时发送 /alicedev",
    }
    fallback = _fallback_text(current_name, current_status, registry)
    renderer = ctx.services.render
    if renderer is None:
        await ctx.reply_text(fallback)
        return
    try:
        image_path = await renderer.render("help", fields)
    except Exception:  # noqa: BLE001 - card rendering is an optional presentation layer
        _LOG.warning("help card rendering failed; using text fallback", exc_info=True)
        await ctx.reply_text(fallback)
        return
    await ctx.event.send(ctx.event.image_result(str(image_path)))


def register(registry: "CommandRegistry", services: "Services") -> None:
    del services

    async def handle(ctx: "CommandContext") -> None:
        await _handle_help(ctx, registry)

    registry.register(
        CommandSpec(
            name="alicedev",
            aliases=(),
            description="显示 alicedev 使用帮助",
            admin_only=False,
            handler=handle,
        )
    )
