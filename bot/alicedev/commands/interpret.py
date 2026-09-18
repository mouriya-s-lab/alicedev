"""``/解读`` — prefetch a GitHub issue/PR and start an AI session."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from alicedev.commands.registry import CommandSpec
from alicedev.github.client import GithubFetchError
from alicedev.templates.registry import (
    ChatVar,
    GithubVar,
    QuotedVar,
    SenderVar,
    TemplateVars,
)

if TYPE_CHECKING:
    from alicedev.commands.context import CommandContext, Services
    from alicedev.commands.registry import CommandRegistry
    from alicedev.github.models import GithubItem


_LOG = logging.getLogger("alicedev.commands.interpret")


async def _handle_interpret(ctx: "CommandContext") -> None:
    text = ctx.args.strip()
    if not text:
        await ctx.reply_text("用法：/解读 <GitHub Issue/PR URL 或 #n>")
        return

    github = ctx.services.github
    if github is None:
        await ctx.reply_text("GitHub 预取未配置。")
        return

    ref = github.try_parse(text)
    if ref is None:
        await ctx.reply_text("请输入 GitHub Issue/PR 链接，或默认仓库的 #n。")
        return

    try:
        item = await github.fetch(ref)
    except GithubFetchError:
        _LOG.exception("GitHub fetch failed: %s", text)
        await ctx.reply_text("读取 GitHub 内容失败，请检查链接或稍后再试。")
        return

    template = ctx.services.templates.by_link(item.kind.value)
    if template is None:
        await ctx.reply_text("缺少对应的 GitHub Prompt 模板。")
        return

    vars = _template_vars(ctx, item)
    record = await ctx.services.sessions.create_and_inject(
        chat_key=ctx.chat_key,
        template=template,
        vars=vars,
        created_by=ctx.user_key,
        platform_message_id=ctx.event.message_obj.message_id,
        sender_key=ctx.user_key,
    )
    await ctx.reply_text(f"已创建 {record.session_ref}")


def _template_vars(ctx: "CommandContext", item: "GithubItem") -> TemplateVars:
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
        github=GithubVar(
            kind=item.kind.value,
            owner=item.owner,
            repo=item.repo,
            number=item.number,
            title=item.title,
            body=item.body,
            labels=item.labels,
            state=item.state,
            url=item.url,
        ),
    )


def _chat_name(ctx: "CommandContext") -> str:
    try:
        group = ctx.event.message_obj.group
        if group is not None and getattr(group, "group_name", None):
            return str(group.group_name)
    except Exception:  # noqa: BLE001
        pass
    return ctx.chat_key


def register(registry: "CommandRegistry", services: "Services") -> None:
    registry.register(
        CommandSpec(
            name="解读",
            aliases=("interpret",),
            description="预取并解读 GitHub Issue 或 Pull Request",
            admin_only=False,
            handler=_handle_interpret,
        )
    )
