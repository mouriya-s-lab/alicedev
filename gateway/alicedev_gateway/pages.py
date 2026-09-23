"""Pure HTML renderers for the gateway's Alice-themed pages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import base64
import hashlib
from html import escape
from typing import assert_never


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
    PASEO_DOWN = "paseo-down"
    REPORT_UNREADABLE = "report-unreadable"


@dataclass(frozen=True, slots=True)
class SessionDetail:
    """Bot ``GET /v1/sessions/{id}`` (ARCHITECTURE §6), parsed at the gateway boundary."""

    label: str
    name: str
    scenario_name: str
    scenario_description: str
    state_label: str
    created_by: str
    created_at: str
    last_activity_at: str | None
    is_current: bool



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
    <footer class="alice-footer">alicedev</footer>
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
            title = "这扇门还没有为你打开"
            eyebrow = "ALICEDEV · NO KEY"
            content = (
                "<h1>这扇门还没有为你打开</h1>"
                "<p>需要有效的 alicedev 分享授权。</p>"
                "<p class=\"alice-hint\">请从群里的分享链接进入；没有链接就在群里发送 "
                "<code>/链接</code>。</p>"
            )
        case ErrorPageKind.REPORT_MISSING:
            title = "这本书里没有这一章"
            eyebrow = "ALICEDEV · NOT FOUND"
            content = "<h1>这本书里没有这一章</h1><p>报告文件不存在。</p>"
        case ErrorPageKind.PATH_MISSING:
            title = "这条小路不通往任何地方"
            eyebrow = "ALICEDEV · NOT FOUND"
            content = "<h1>这条小路不通往任何地方</h1><p>网关路径不存在。</p>"
        case ErrorPageKind.PASEO_DOWN:
            title = "怀表停了一会儿"
            eyebrow = "ALICEDEV · THE CLOCK STOPPED"
            content = (
                "<h1>怀表停了一会儿</h1>"
                "<p>paseo 上游暂时不可用。</p>"
                "<p class=\"alice-hint\">工作区正在重启或暂时连不上，稍等片刻再刷新；"
                "一直打不开就在群里说一声。</p>"
            )
        case ErrorPageKind.REPORT_UNREADABLE:
            title = "这一章的墨迹糊掉了"
            eyebrow = "ALICEDEV · SMUDGED INK"
            content = (
                "<h1>这一章的墨迹糊掉了</h1>"
                "<p>报告读取失败。</p>"
                "<p class=\"alice-hint\">稍后再打开试试；一直这样就在群里说一声。</p>"
            )
        case _:
            assert_never(kind)

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


def render_session_page(detail: SessionDetail | None, target: str) -> str:
    """Render the session page a redeemed link lands on (ARCHITECTURE §8)."""

    action = (
        '<p class="alice-action">'
        f'<a class="alice-button" href="{escape(target, quote=True)}">进入会话</a></p>'
    )
    if detail is None:
        content = f"""<div class="alice-card-heading">
  <img class="alice-emblem" src="{ALICE_EMBLEM_SRC}" alt="">
  <h1 class="alice-session-title">会话信息暂时取不到</h1>
</div>
<p>bot 暂时没有返回这个会话的信息，稍后刷新再看；进入会话不受影响。</p>
{action}"""
        return _page_shell(
            title="alicedev 会话",
            eyebrow="ALICEDEV · SESSION",
            content=content,
            image_src=ALICE_HERO_SRC,
            image_alt="持着怀表的哥特洛丽塔爱丽丝",
            image_class="alice-art--hero",
        )

    # The scenario id (e.g. "requirement") is internal; its description is what people read.
    scenario = escape(detail.scenario_description or detail.scenario_name)
    current = (
        '<span class="alice-status alice-status--current">本群当前会话</span>'
        if detail.is_current
        else ""
    )
    content = f"""<div class="alice-card-heading">
  <img class="alice-emblem" src="{ALICE_EMBLEM_SRC}" alt="">
  <h1 class="alice-session-title">{escape(detail.name)}</h1>
</div>
<p class="alice-subtitle">{scenario}</p>
<div class="alice-status-block">
  <span class="alice-status alice-status--state">{escape(detail.state_label)}</span>
  {current}
</div>
{action}
<dl class="alice-facts">
  <dt>创建人</dt><dd>{escape(detail.created_by)}</dd>
  <dt>创建时间</dt><dd>{escape(detail.created_at)}</dd>
  <dt>最近活动</dt><dd>{escape(detail.last_activity_at or "—")}</dd>
</dl>"""
    return _page_shell(
        title=f"{detail.label} {detail.name} · alicedev",
        eyebrow=f"ALICEDEV · {detail.label}",
        content=content,
        image_src=ALICE_HERO_SRC,
        image_alt="持着怀表的哥特洛丽塔爱丽丝",
        image_class="alice-art--hero",
    )
