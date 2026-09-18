"""Paginated image-card commands for requirements and favorites."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from alicedev.commands.context import CommandContext
from alicedev.commands.registry import CommandRegistry, CommandSpec
from alicedev.images import ImageDownloadError, image_data_uri
from alicedev.render.pagination import parse_page
from alicedev.store.favorites_repo import FavoriteRecord, FavoritesRepo
from alicedev.store.requirements_repo import RequirementRecord, RequirementsRepo

if TYPE_CHECKING:
    from alicedev.commands.context import Services


@dataclass(frozen=True)
class CardRow:
    """Typed view-model consumed by the Jinja card templates."""

    id: int
    text: str
    author_name: str
    saver_name: str = ""
    created_at: str = ""
    status: str = ""
    images: tuple[str, ...] = ()


def _page_arg(args: str) -> int:
    token = args.strip().split(maxsplit=1)[0] if args.strip() else None
    return parse_page(token)


def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d %H:%M")


def _thumbnail(reference: str, context: CommandContext) -> str:
    if reference.startswith(("data:image/", "http://", "https://")):
        return reference
    path = Path(reference).expanduser()
    if not path.is_absolute():
        path = context.services.config.images_root / path
    try:
        return image_data_uri(path)
    except ImageDownloadError:
        return ""


def _thumbnails(references: Sequence[str], context: CommandContext) -> tuple[str, ...]:
    return tuple(
        thumbnail
        for reference in references
        if (thumbnail := _thumbnail(reference, context))
    )


def _requirement_row(record: RequirementRecord, context: CommandContext) -> CardRow:
    return CardRow(
        id=record.id,
        text=record.text,
        author_name=record.author_name,
        created_at=_format_timestamp(record.created_at),
        status=record.status,
        images=_thumbnails(record.images, context),
    )


def _favorite_row(record: FavoriteRecord, context: CommandContext) -> CardRow:
    return CardRow(
        id=record.id,
        text=record.text,
        author_name=record.author_name,
        saver_name=record.saver_name,
        created_at=_format_timestamp(record.created_at),
        images=_thumbnails(record.images, context),
    )


async def _send_card(context: CommandContext, template: str, fields: dict[str, object]) -> None:
    renderer = context.services.render
    if renderer is None:
        await context.reply_text("卡片渲染服务尚未就绪，请稍后再试")
        return
    image_path = await renderer.render(template, fields)
    await context.event.send(context.event.image_result(str(image_path)))


async def handle_favorites_list(context: CommandContext) -> None:
    page = await FavoritesRepo(context.services.store).list_page(
        context.chat_key, _page_arg(context.args)
    )
    rows = tuple(_favorite_row(record, context) for record in page.items)
    await _send_card(
        context,
        "favorites_list",
        {
            "title": "收藏夹",
            "subtitle": f"共 {page.total} 条收藏",
            "rows": rows,
            "footer": f"第 {page.page}/{page.pages} 页 · /收藏夹 {page.page}",
        },
    )


async def handle_requirements_list(context: CommandContext) -> None:
    page = await RequirementsRepo(context.services.store).list_page(
        context.chat_key, _page_arg(context.args)
    )
    rows = tuple(_requirement_row(record, context) for record in page.items)
    await _send_card(
        context,
        "requirements_list",
        {
            "title": "需求列表",
            "subtitle": f"共 {page.total} 条需求",
            "rows": rows,
            "footer": f"第 {page.page}/{page.pages} 页 · /需求列表 {page.page}",
        },
    )


def register(registry: CommandRegistry, services: "Services") -> None:
    """Register both paginated list commands."""

    del services
    registry.register(
        CommandSpec(
            name="收藏夹",
            aliases=(),
            description="查看当前聊天的收藏夹",
            admin_only=False,
            handler=handle_favorites_list,
        )
    )
    registry.register(
        CommandSpec(
            name="需求列表",
            aliases=(),
            description="查看当前聊天的需求列表",
            admin_only=False,
            handler=handle_requirements_list,
        )
    )
