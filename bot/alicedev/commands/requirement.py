"""``/需求`` command: record a requirement and start a paseo session."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from alicedev.commands.registry import CommandSpec
from alicedev.templates.registry import ChatVar, QuotedVar, SenderVar, TemplateVars

if TYPE_CHECKING:
    from alicedev.commands.context import CommandContext, Services
    from alicedev.commands.registry import CommandRegistry


def build_template_vars(ctx: "CommandContext", *, session_ref: str = "") -> TemplateVars:
    quoted = None
    if ctx.quoted is not None:
        quoted = QuotedVar(
            sender=ctx.quoted.sender_name,
            text=ctx.quoted.text,
            images=ctx.quoted.image_urls,
        )
    return TemplateVars(
        text=ctx.args,
        sender=SenderVar(id=ctx.user_key, name=ctx.sender_name),
        chat=ChatVar(key=ctx.chat_key, name=_chat_name(ctx)),
        quoted=quoted,
        session_ref=session_ref,
    )


def _chat_name(ctx: "CommandContext") -> str:
    try:
        group = ctx.event.message_obj.group
        if group is not None and getattr(group, "group_name", None):
            return str(group.group_name)
    except Exception:  # noqa: BLE001
        pass
    return ctx.chat_key


async def _handle_requirement(ctx: "CommandContext") -> None:
    services = ctx.services
    template = services.templates.by_command("需求")
    if template is None:
        await ctx.reply_text("需求模板缺失，请检查部署。")
        return
    if not ctx.args.strip():
        await ctx.reply_text("用法：/需求 <内容>")
        return

    vars = build_template_vars(ctx)
    record = await services.sessions.create_and_inject(
        chat_key=ctx.chat_key,
        template=template,
        vars=vars,
        created_by=ctx.user_key,
        platform_message_id=ctx.event.message_obj.message_id,
        sender_key=ctx.user_key,
    )

    if template.record == "requirement":
        rid = await services.store.next_id("seq_requirements")
        await services.store.execute(
            "INSERT INTO requirements (id, chat_key, session_ref, author_key, "
            "author_name, text, images, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'open')",
            (rid, ctx.chat_key, record.session_ref, ctx.user_key, ctx.sender_name,
             ctx.args, json.dumps([])),
        )

    await ctx.reply_text(f"已创建 {record.session_ref}")


def register(registry: "CommandRegistry", services: "Services") -> None:
    registry.register(
        CommandSpec(
            name="需求",
            aliases=("req",),
            description="记录群友需求并交给 AI 分析",
            admin_only=False,
            handler=_handle_requirement,
        )
    )
