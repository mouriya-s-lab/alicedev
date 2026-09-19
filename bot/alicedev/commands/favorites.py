"""``/收藏`` command: persist a quoted message and its images."""

from __future__ import annotations

from typing import TYPE_CHECKING

from alicedev.commands.context import CommandContext
from alicedev.commands.registry import CommandRegistry, CommandSpec
from alicedev.images import ImageDownloadError, download_quoted_images
from alicedev.store.favorites_repo import FavoritesRepo

if TYPE_CHECKING:
    from alicedev.commands.context import Services


_USAGE = "用法：/收藏（请引用一条消息后再发送）"


async def handle_favorite(context: CommandContext) -> None:
    """Save the quoted message, including any materialized images."""

    quoted = context.quoted
    if quoted is None:
        await context.reply_text(_USAGE)
        return

    try:
        stored_images = await download_quoted_images(
            quoted.image_urls, context.services.config.images_root
        )
    except ImageDownloadError:
        await context.reply_text("收藏失败：引用消息中的图片无法下载")
        return

    record_id = await FavoritesRepo(context.services.store).insert(
        chat_key=context.chat_key,
        saver_key=context.user_key,
        saver_name=context.sender_name,
        author_key=quoted.sender_key,
        author_name=quoted.sender_name,
        text=quoted.text,
        images=[str(path) for path in stored_images],
        platform_message_id=quoted.platform_message_id,
    )
    await context.reply_text(f"已收藏 #{record_id}")


def register(registry: CommandRegistry, services: "Services") -> None:
    """Register favorites commands during plugin initialization."""

    del services
    registry.register(
        CommandSpec(
            name="收藏",
            aliases=(),
            description="收藏一条引用消息（可包含图片）",
            admin_only=False,
            handler=handle_favorite,
        )
    )
