"""Pure HTML renderers for the gateway's Alice-themed pages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import base64
import hashlib
from html import escape, unescape
from typing import Sequence


ALICE_STATIC_PREFIX = "/_alicedev/static/alice"
ALICE_CSS_HREF = f"{ALICE_STATIC_PREFIX}/alice.css"
ALICE_ICON_HREF = f"{ALICE_STATIC_PREFIX}/alice-icon.png"
ALICE_HERO_SRC = f"{ALICE_STATIC_PREFIX}/alice-hero.webp"
ALICE_TORN_SRC = f"{ALICE_STATIC_PREFIX}/alice-torn.webp"
ALICE_EMBLEM_SRC = f"{ALICE_STATIC_PREFIX}/alice-emblem.webp"

PREVIEW_SCRIPT = 'document.getElementById("redeem").submit();'
PREVIEW_SCRIPT_HASH = base64.b64encode(
    hashlib.sha256(PREVIEW_SCRIPT.encode("utf-8")).digest()
).decode("ascii")

THEMED_CSP = (
    "default-src 'none'; "
    "style-src 'self'; "
    "img-src 'self'; "
    "font-src 'self'; "
    "form-action 'self'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "object-src 'none'"
)


class ErrorPageKind(Enum):
    """Closed set of user-facing gateway error pages."""

    LINK_SPENT = "link-spent"
    NEEDS_INVITATION = "needs-invitation"
    REPORT_MISSING = "report-missing"
    PATH_MISSING = "path-missing"


@dataclass(frozen=True, slots=True)
class BotCommand:
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class BotScenario:
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class BotStatus:
    ok: bool
    generation: str
    uptime_s: str
    platforms: tuple[str, ...]
    commands: tuple[BotCommand, ...]
    scenarios: tuple[BotScenario, ...]
    active_sessions: str
    queued_sessions: str
    error: str | None = None

    @classmethod
    def unavailable(cls, error: str) -> "BotStatus":
        return cls(
            ok=False,
            generation="—",
            uptime_s="—",
            platforms=(),
            commands=(),
            scenarios=(),
            active_sessions="—",
            queued_sessions="—",
            error=error,
        )


CONFIRM_CSP = f"{THEMED_CSP}; script-src 'sha256-{PREVIEW_SCRIPT_HASH}'"

THEMED_HEADERS: dict[str, str] = {
    "Content-Security-Policy": THEMED_CSP,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex",
}

CONFIRM_HEADERS: dict[str, str] = {
    **THEMED_HEADERS,
    "Content-Security-Policy": CONFIRM_CSP,
}


def _page_shell(
    *,
    title: str,
    eyebrow: str,
    content: str,
    image_src: str,
    image_alt: str,
    image_class: str,
    card_class: str = "",
    layout_class: str = "",
) -> str:
    escaped_title = escape(title)
    escaped_eyebrow = escape(eyebrow)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#0f0b12">
  <link rel="icon" type="image/png" href="{ALICE_ICON_HREF}">
  <link rel="apple-touch-icon" href="{ALICE_ICON_HREF}">
  <link rel="stylesheet" href="{ALICE_CSS_HREF}">
  <title>{escaped_title}</title>
</head>
<body class="alice-page">
  <main class="alice-shell">
    <div class="alice-layout {layout_class}">
      <figure class="alice-art {image_class}">
        <img src="{image_src}" alt="{image_alt}">
      </figure>
      <section class="alice-card {card_class}">
        <span class="alice-suit alice-suit--top-right" aria-hidden="true">♦</span>
        <span class="alice-suit alice-suit--bottom-left" aria-hidden="true">♣</span>
        <p class="alice-eyebrow">{escaped_eyebrow}</p>
        {content}
      </section>
    </div>
    <footer class="alice-footer">alicedev · 仙境工作室</footer>
  </main>
</body>
</html>
"""


def render_token_preview(action: str) -> str:
    """Render the one-time-link confirmation page."""

    escaped_action = escape(action, quote=True)
    content = f"""<h1>正在为你翻开书页……</h1>
<p>爱丽丝在前面带路。页面没有自动跳转的话，按下面的按钮跟上她。</p>
<form id="redeem" method="post" action="{escaped_action}">
  <button class="alice-button" type="submit">跟上爱丽丝</button>
</form>
<script>{PREVIEW_SCRIPT}</script>"""
    return _page_shell(
        title="正在翻开 alicedev",
        eyebrow="ALICEDEV · WHITE RABBIT",
        content=content,
        image_src=ALICE_HERO_SRC,
        image_alt="荆棘与怀表之间的爱丽丝",
        image_class="alice-art--hero",
        card_class="alice-card--confirm",
    )


def render_error_page(kind: ErrorPageKind) -> str:
    """Render one of the closed set of themed gateway error pages."""

    match kind:
        case ErrorPageKind.LINK_SPENT:
            title = "这一页已经被撕掉了"
            eyebrow = "ALICEDEV · A TORN PAGE"
            content = (
                "<h1>这一页已经被撕掉了</h1>"
                "<p>链接无效、已使用或已过期。每个链接只能打开一次。</p>"
                "<p class=\"alice-hint\">回到群里发送 <code>/链接</code>，爱丽丝会再给你一张。</p>"
            )
        case ErrorPageKind.NEEDS_INVITATION:
            title = "你还没有茶会的请柬"
            eyebrow = "ALICEDEV · NO INVITATION"
            content = (
                "<h1>你还没有茶会的请柬</h1>"
                "<p>需要有效的 alicedev 分享授权。</p>"
                "<p class=\"alice-hint\">请从群里的分享链接进入；没有链接就在群里发送 "
                "<code>/链接</code>。</p>"
            )
        case ErrorPageKind.REPORT_MISSING:
            title = "这本书里没有这一章"
            eyebrow = "ALICEDEV · LOST IN WONDERLAND"
            content = "<h1>这本书里没有这一章</h1><p>报告文件不存在。</p>"
        case ErrorPageKind.PATH_MISSING:
            title = "这条小路不通往任何地方"
            eyebrow = "ALICEDEV · LOST IN WONDERLAND"
            content = "<h1>这条小路不通往任何地方</h1><p>网关路径不存在。</p>"
        case _ as unreachable:
            raise AssertionError(f"unhandled error page kind: {unreachable!r}")

    return _page_shell(
        title=title,
        eyebrow=eyebrow,
        content=content,
        image_src=ALICE_TORN_SRC,
        image_alt="撕开的童话书页与探头的爱丽丝",
        image_class="alice-art--torn",
        card_class="alice-card--error",
        layout_class="alice-layout--error",
    )


def render_status(status: BotStatus, target: str | None) -> str:
    """Render the authenticated bot status page."""

    state_class = "alice-status--ok" if status.ok else "alice-status--degraded"
    state_label = "♠ 运行中" if status.ok else "♥ 降级（bot 不可用）"
    error = (
        f'<p class="alice-status-error">{escape(status.error)}</p>'
        if status.error
        else ""
    )
    platforms = _render_platforms(status.platforms)
    commands = _render_items(status.commands)
    scenarios = _render_items(status.scenarios)
    uptime = _humanize_uptime(status.uptime_s)
    raw_uptime = escape(unescape(status.uptime_s), quote=True)
    action = (
        f'<a class="alice-button" href="{escape(target, quote=True)}">进入会话</a>'
        if target
        else '<span class="alice-muted">没有可进入的会话目标</span>'
    )
    content = f"""<div class="alice-card-heading">
  <img class="alice-emblem" src="{ALICE_EMBLEM_SRC}" alt="">
  <h1>欢迎回到仙境</h1>
</div>
<div class="alice-status-block" aria-label="bot 状态">
  <span class="alice-status {state_class}">{state_label}</span>
  {error}
</div>
<p class="alice-action">{action}</p>
<div class="alice-stat-grid">
  <div class="alice-stat"><span>运行代数</span><strong>{status.generation}</strong></div>
  <div class="alice-stat"><span>运行时间</span><strong title="{raw_uptime}">{uptime}</strong></div>
  <div class="alice-stat"><span>进行中会话</span><strong>{status.active_sessions}</strong></div>
  <div class="alice-stat"><span>排队</span><strong>{status.queued_sessions}</strong></div>
</div>
<div class="alice-platforms">
  <span class="alice-stat-label">平台</span>
  {platforms}
</div>
<div class="alice-status-sections">
  <section class="alice-list-section">
    <h2>可用指令</h2>
    {commands}
  </section>
  <section class="alice-list-section">
    <h2>场景</h2>
    {scenarios}
  </section>
</div>"""
    return _page_shell(
        title="alicedev 状态",
        eyebrow="ALICEDEV · TEA PARTY",
        content=content,
        image_src=ALICE_HERO_SRC,
        image_alt="持着怀表的哥特洛丽塔爱丽丝",
        image_class="alice-art--hero alice-art--status",
        card_class="alice-card--status",
    )


def _render_platforms(values: Sequence[str]) -> str:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    if not unique:
        return "—"
    return "<span class=\"alice-tag-list\">" + "".join(
        f'<span class="alice-tag">{escape(value)}</span>' for value in unique
    ) + "</span>"


def _render_items(items: Sequence[BotCommand | BotScenario]) -> str:
    if not items:
        return '<p class="alice-muted">暂无</p>'

    chip_rows: list[str] = []
    detail_rows: list[str] = []
    for item in items:
        name = item.name if item.name.startswith("/") else f"/{item.name}"
        if item.description:
            detail_rows.append(
                f'<li><code>{escape(name)}</code>'
                f'<span class="alice-item-description">{escape(item.description)}</span></li>'
            )
        else:
            chip_rows.append(f'<li><code>{escape(name)}</code></li>')

    groups: list[str] = []
    if chip_rows:
        groups.append(
            '<ul class="alice-command-list alice-command-list--chips">'
            + "".join(chip_rows)
            + "</ul>"
        )
    if detail_rows:
        groups.append(
            '<ul class="alice-command-list alice-command-list--described">'
            + "".join(detail_rows)
            + "</ul>"
        )
    return "".join(groups)


def _humanize_uptime(value: str) -> str:
    """Turn a numeric seconds value into a compact Chinese duration."""

    raw = unescape(value)
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return escape(raw)
    if seconds < 0:
        return escape(raw)
    total = int(seconds)
    days, remainder = divmod(total, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, seconds_left = divmod(remainder, 60)
    if days:
        parts = [f"{days} 天"]
        if hours:
            parts.append(f"{hours} 小时")
        if minutes:
            parts.append(f"{minutes} 分钟")
        return " ".join(parts)
    if hours:
        parts = [f"{hours} 小时"]
        if minutes:
            parts.append(f"{minutes} 分钟")
        return " ".join(parts)
    if minutes:
        return f"{minutes} 分钟"
    return f"{seconds_left} 秒"
