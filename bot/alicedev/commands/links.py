"""``/链接`` — issue one-time Paseo workspace links for administrators."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from urllib.parse import quote

from alicedev.commands.registry import CommandSpec
from alicedev.store.sessions_repo import SessionRecord, SessionStatus

if TYPE_CHECKING:
    from alicedev.commands.context import CommandContext, Services
    from alicedev.commands.registry import CommandRegistry


_LOG = logging.getLogger("alicedev.commands.links")
_SESSION_REF = re.compile(r"^s_[a-z2-7]{10}$")
_TOKEN_TTL_S = 21600


async def _handle_links(ctx: "CommandContext") -> None:
    services = ctx.services
    session_ref, valid = _explicit_session_ref(ctx.args)
    if not valid:
        await ctx.reply_text("用法：/链接 [s_ref]")
        return

    if session_ref is None:
        record = await services.sessions.current_for_chat(ctx.chat_key)
    else:
        record = await _session_by_ref(services, session_ref, ctx.chat_key)
    if record is None:
        if session_ref is None:
            await ctx.reply_text("当前群没有可分享的当前会话。")
        else:
            await ctx.reply_text("未找到这个会话。")
        return
    if not record.agent_id or not record.workspace_id or not record.server_id:
        await ctx.reply_text("该会话尚未准备好分享。")
        return

    gateway = services.gateway
    if gateway is None:
        await ctx.reply_text("分享网关未配置。")
        return

    target = _paseo_target(record)
    sent = 0
    for user_key, name, platform_id in _recipients(ctx):
        try:
            issued = await gateway.issue_token(
                target=target,
                user_key=user_key,
                ttl_s=_TOKEN_TTL_S,
            )
            await _record_token(
                services,
                session_ref=record.session_ref,
                user_key=user_key,
                issued_by=ctx.user_key,
                target=target,
            )
            await _send_link(
                ctx,
                platform_id=platform_id,
                name=name,
                session_name=record.name,
                url=issued.url,
            )
            sent += 1
        except Exception:  # noqa: BLE001 - one recipient must not block others
            _LOG.exception(
                "gateway link issuance failed: session=%s user=%s",
                record.session_ref,
                user_key,
            )

    if sent == 0:
        await ctx.reply_text("分享链接生成失败，请稍后再试。")


async def _session_by_ref(
    services: "Services",
    session_ref: str,
    chat_key: str,
) -> SessionRecord | None:
    row = await services.store.fetch_one(
        "SELECT session_ref, chat_key, template, name, provider, model, thinking, "
        "agent_id, workspace_id, server_id, status, created_by, created_at, "
        "last_activity_at FROM sessions WHERE session_ref = ? AND chat_key = ?",
        (session_ref, chat_key),
    )
    if row is None:
        return None
    return SessionRecord(
        session_ref=str(row[0]),
        chat_key=str(row[1]),
        template=str(row[2]),
        name=str(row[3]),
        provider=str(row[4]) if row[4] is not None else None,
        model=str(row[5]) if row[5] is not None else None,
        thinking=str(row[6]) if row[6] is not None else None,
        agent_id=str(row[7]) if row[7] is not None else None,
        workspace_id=str(row[8]) if row[8] is not None else None,
        server_id=str(row[9]) if row[9] is not None else None,
        status=SessionStatus(str(row[10])),
        created_by=str(row[11]),
        created_at=row[12],
        last_activity_at=row[13],
    )


def _explicit_session_ref(args: str) -> tuple[str | None, bool]:
    parts = args.split()
    refs = [part for part in parts if _SESSION_REF.fullmatch(part)]
    non_mentions = [part for part in parts if not part.startswith("@") and part not in refs]
    if len(refs) > 1 or non_mentions:
        return None, False
    return (refs[0] if refs else None), True


def _paseo_target(record: SessionRecord) -> str:
    server_id = quote(record.server_id or "", safe="")
    workspace_id = quote(record.workspace_id or "", safe="")
    agent_id = quote(record.agent_id or "", safe="")
    return (
        f"/h/{server_id}/workspace/{workspace_id}"
        f"?open=agent%3A{agent_id}&embed=1"
    )


def _recipients(ctx: "CommandContext") -> list[tuple[str, str, str]]:
    if ctx.mentions:
        return [
            (
                mention.user_key,
                mention.name or mention.user_key.rsplit(":", 1)[-1],
                mention.user_key.rsplit(":", 1)[-1],
            )
            for mention in ctx.mentions
        ]
    return [(ctx.user_key, ctx.sender_name, ctx.user_key.rsplit(":", 1)[-1])]


async def _record_token(
    services: "Services",
    *,
    session_ref: str,
    user_key: str,
    issued_by: str,
    target: str,
) -> None:
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(seconds=_TOKEN_TTL_S)
    async with services.store.lock:
        await services.store.execute(
            "INSERT INTO tokens_issued "
            "(token_id, session_ref, user_key, issued_by, target, issued_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"t_{uuid.uuid4().hex}",
                session_ref,
                user_key,
                issued_by,
                target,
                issued_at.replace(tzinfo=None),
                expires_at.replace(tzinfo=None),
            ),
        )


async def _send_link(
    ctx: "CommandContext",
    *,
    platform_id: str,
    name: str,
    session_name: str,
    url: str,
) -> None:
    message = f"会话：「{session_name}」\n{url}"
    try:
        from astrbot.api.message_components import At, Plain

        await ctx.event.send(
            ctx.event.chain_result([At(qq=platform_id, name=name), Plain(message)])
        )
    except (ImportError, AttributeError):
        # Lightweight harnesses may not install AstrBot's component package.
        await ctx.reply_text(message)


def register(registry: "CommandRegistry", services: "Services") -> None:
    registry.register(
        CommandSpec(
            name="链接",
            aliases=("link",),
            description="为会话生成一次性工作区分享链接",
            admin_only=True,
            handler=_handle_links,
        )
    )
