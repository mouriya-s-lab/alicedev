"""``/alicedev`` — show registered commands and prompt templates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from alicedev.commands.registry import CommandSpec
from alicedev.templates.registry import Command, Link

if TYPE_CHECKING:
    from alicedev.commands.context import CommandContext, Services
    from alicedev.commands.registry import CommandRegistry


async def _handle_help(ctx: "CommandContext", registry: "CommandRegistry") -> None:
    lines = ["alicedev 可用指令："]
    for spec in registry.all():
        aliases = f"（别名：{'、'.join('/' + a for a in spec.aliases)}）" if spec.aliases else ""
        permission = " [管理员]" if spec.admin_only else ""
        lines.append(f"/{spec.name}{permission} — {spec.description}{aliases}")

    lines.append("")
    lines.append("Prompt 模板：")
    templates = ctx.services.templates.all()
    if not templates:
        lines.append("（暂无模板）")
    for template in templates:
        trigger = template.trigger
        if isinstance(trigger, Command):
            trigger_text = f"/{trigger.name}"
            if trigger.aliases:
                trigger_text += f"（别名：{'、'.join('/' + a for a in trigger.aliases)}）"
        elif isinstance(trigger, Link):
            trigger_text = f"链接触发：{trigger.kind}"
        else:  # pragma: no cover - Trigger is a closed union
            trigger_text = "其他触发"
        lines.append(f"- {template.name} · {trigger_text}：{template.description}")

    await ctx.reply_text("\n".join(lines))


def register(registry: "CommandRegistry", services: "Services") -> None:
    async def handle(ctx: "CommandContext") -> None:
        await _handle_help(ctx, registry)

    registry.register(
        CommandSpec(
            name="alicedev",
            aliases=(),
            description="显示 alicedev 指令和 Prompt 模板",
            admin_only=False,
            handler=handle,
        )
    )
