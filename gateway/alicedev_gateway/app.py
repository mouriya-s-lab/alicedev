"""aiohttp application for token-gated Paseo and public reports."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from html import escape
import hashlib
import json
import logging
from pathlib import Path
import time
from typing import Mapping
from urllib.parse import quote, urlsplit
from aiohttp import ClientError, ClientSession, ClientTimeout, web

from .config import GatewayConfig
from .proxy import SELF_HOSTED_MANIFEST, PaseoProxy
from .reports import ReportRenderer, resolve_report_asset
from .tokens import COOKIE_NAME, COOKIE_MAX_AGE, SignedCookieCodec, TokenTable


_LOGGER = logging.getLogger("alicedev_gateway.access")
_SWEEP_INTERVAL_SECONDS = 60.0
_STATIC_FILES = frozenset({"mermaid.min.js", "pygments.css"})
_PREVIEW_SCRIPT = 'document.getElementById("redeem").submit();'
_PREVIEW_SCRIPT_HASH = base64.b64encode(
    hashlib.sha256(_PREVIEW_SCRIPT.encode("utf-8")).digest()
).decode("ascii")
_PREVIEW_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; "
        f"script-src 'sha256-{_PREVIEW_SCRIPT_HASH}'; "
        "form-action 'self'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'; "
        "object-src 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex",
}


@dataclass(frozen=True, slots=True)
class BotCommand:
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class BotTemplate:
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class BotStatus:
    ok: bool
    generation: str
    uptime_s: str
    platforms: tuple[str, ...]
    commands: tuple[BotCommand, ...]
    templates: tuple[BotTemplate, ...]
    active_sessions: str
    closed_sessions: str
    error: str | None = None

    @classmethod
    def unavailable(cls, error: str) -> "BotStatus":
        return cls(
            ok=False,
            generation="—",
            uptime_s="—",
            platforms=(),
            commands=(),
            templates=(),
            active_sessions="—",
            closed_sessions="—",
            error=error,
        )


def create_app(config: GatewayConfig) -> web.Application:
    tokens = TokenTable()
    cookies = SignedCookieCodec(config.secret)
    renderer = ReportRenderer()
    app = web.Application(middlewares=[access_log_middleware, cookie_gate_middleware])
    app["gateway_config"] = config
    app["token_table"] = tokens
    app["cookie_codec"] = cookies
    app["report_renderer"] = renderer
    app.router.add_get("/t/{token}", preview_token)
    app.router.add_post("/t/{token}", redeem_token)
    app.router.add_post("/internal/tokens", issue_token)
    app.router.add_get("/_alicedev/health", health)
    app.router.add_get("/_alicedev/", status_page)
    app.router.add_get("/_alicedev/r/{report_id}/{basename}", report)
    app.router.add_get("/_alicedev/static/{asset}", static_asset)
    app.router.add_get("/_paseo/hosts.json", self_hosted_manifest)
    app.router.add_route("*", "/{path_info:.*}", proxy)
    app.on_startup.append(start_runtime)
    app.on_cleanup.append(stop_runtime)
    return app


@web.middleware
async def access_log_middleware(
    request: web.Request,
    handler: web.Handler,
) -> web.StreamResponse:
    started = time.monotonic()
    status = 500
    try:
        response = await handler(request)
        status = response.status
        return response
    except web.HTTPException as exc:
        status = exc.status
        raise
    finally:
        elapsed_ms = (time.monotonic() - started) * 1000
        # Never log query strings or request headers.  In particular this keeps
        # cookies out of logs and masks the one-time token path segment.
        path = "/t/***" if request.path.startswith("/t/") else request.path
        _LOGGER.info("%s %s -> %s (%.1fms)", request.method, path, status, elapsed_ms)


@web.middleware
async def cookie_gate_middleware(
    request: web.Request,
    handler: web.Handler,
) -> web.StreamResponse:
    path = request.path
    if path == "/internal/tokens":
        if request.method != "POST":
            raise web.HTTPMethodNotAllowed(request.method, {"POST"})
        return await handler(request)
    if path.startswith("/t/"):
        if request.method not in {"GET", "HEAD", "POST"}:
            raise web.HTTPMethodNotAllowed(request.method, {"GET", "HEAD", "POST"})
        return await handler(request)

    codec: SignedCookieCodec = request.app["cookie_codec"]
    if not codec.verify(request.cookies.get(COOKIE_NAME)):
        raise web.HTTPForbidden(text="需要有效的 alicedev 分享授权。\n")
    return await handler(request)

async def start_runtime(app: web.Application) -> None:
    config: GatewayConfig = app["gateway_config"]
    session = ClientSession(timeout=ClientTimeout(total=30), auto_decompress=False)
    app["http_session"] = session
    app["paseo_proxy"] = PaseoProxy(config=config, session=session)
    app["token_sweeper"] = asyncio.create_task(_sweep_tokens(app["token_table"]))


async def stop_runtime(app: web.Application) -> None:
    task: asyncio.Task[None] = app["token_sweeper"]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    session: ClientSession = app["http_session"]
    await session.close()


async def _sweep_tokens(tokens: TokenTable) -> None:
    try:
        while True:
            await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
            tokens.sweep()
    except asyncio.CancelledError:
        return


async def issue_token(request: web.Request) -> web.Response:
    config: GatewayConfig = request.app["gateway_config"]
    supplied = request.headers.get("X-Alicedev-Token", "")
    if not _constant_time_equal(supplied, config.internal_token):
        raise web.HTTPForbidden(text="无效的内部凭据。\n")

    payload = await _read_json_object(request)
    target = payload.get("target")
    user_key = payload.get("user_key")
    ttl_value = payload.get("ttl_s")
    if not isinstance(target, str) or not isinstance(user_key, str):
        raise web.HTTPBadRequest(text="target 与 user_key 必须是字符串。\n")

    ttl_s: int | float | None
    if ttl_value is None:
        ttl_s = None
    elif isinstance(ttl_value, (int, float)) and not isinstance(ttl_value, bool):
        ttl_s = ttl_value
    else:
        raise web.HTTPBadRequest(text="ttl_s 必须是正数。\n")

    tokens: TokenTable = request.app["token_table"]
    try:
        issued = tokens.issue(target=target, user_key=user_key, ttl_s=ttl_s)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=f"{exc}\n") from exc

    url = f"{_public_scheme(config)}://{config.public_host}/t/{issued.token}"
    return web.json_response({"token": issued.token, "url": url})


async def preview_token(request: web.Request) -> web.Response:
    token = request.match_info["token"]
    tokens: TokenTable = request.app["token_table"]
    if not tokens.validate_token(token) or tokens.peek(token) is None:
        raise web.HTTPForbidden(text="链接无效、已使用或已过期。\n")
    return web.Response(
        text=_render_token_preview(request.path),
        content_type="text/html",
        charset="utf-8",
        headers=_PREVIEW_HEADERS,
    )


async def redeem_token(request: web.Request) -> web.StreamResponse:
    token = request.match_info["token"]
    tokens: TokenTable = request.app["token_table"]
    record = tokens.consume(token)
    if record is None:
        raise web.HTTPForbidden(text="链接无效、已使用或已过期。\n")

    codec: SignedCookieCodec = request.app["cookie_codec"]
    response = web.HTTPSeeOther(
        location=f"/_alicedev/?go={quote(record.target, safe='')}"
    )
    response.set_cookie(
        COOKIE_NAME,
        codec.issue(user_key=record.user_key),
        max_age=COOKIE_MAX_AGE,
        path="/",
        secure=True,
        httponly=True,
        samesite="Lax",
    )
    raise response


async def health(request: web.Request) -> web.Response:
    del request
    return web.json_response({"ok": True, "service": "alicedev-gateway"})


async def self_hosted_manifest(request: web.Request) -> web.Response:
    del request
    return web.json_response(list(SELF_HOSTED_MANIFEST), headers={"Cache-Control": "no-store"})


async def status_page(request: web.Request) -> web.Response:
    config: GatewayConfig = request.app["gateway_config"]
    session: ClientSession = request.app["http_session"]
    status = await _fetch_bot_status(session, config)
    requested_target = request.query.get("go", "")
    target = requested_target if TokenTable.validate_target(requested_target) else None
    return web.Response(
        text=_render_status(status, target),
        content_type="text/html",
        charset="utf-8",
        headers={"Referrer-Policy": "no-referrer"},
    )


async def report(request: web.Request) -> web.StreamResponse:
    config: GatewayConfig = request.app["gateway_config"]
    asset = resolve_report_asset(
        config.reports_published_root,
        request.match_info["report_id"],
        request.match_info["basename"],
    )
    if asset is None:
        raise web.HTTPNotFound(text="报告文件不存在。\n")

    headers = {
        "X-Robots-Tag": "noindex",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }
    if asset.path.suffix.lower() == ".md":
        try:
            source = asset.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise web.HTTPInternalServerError(text="报告读取失败。\n") from exc
        renderer: ReportRenderer = request.app["report_renderer"]
        return web.Response(
            text=renderer.render(source, title=asset.path.name),
            content_type="text/html",
            charset="utf-8",
            headers=headers,
        )

    return web.FileResponse(asset.path, headers={**headers, "Content-Type": asset.content_type})


async def static_asset(request: web.Request) -> web.StreamResponse:
    asset_name = request.match_info["asset"]
    if asset_name not in _STATIC_FILES:
        raise web.HTTPNotFound(text="静态资源不存在。\n")
    static_path = Path(__file__).resolve().parent.parent / "static" / asset_name
    if not static_path.is_file():
        raise web.HTTPNotFound(text="静态资源不存在。\n")
    content_type = "application/javascript; charset=utf-8" if asset_name.endswith(".js") else "text/css; charset=utf-8"
    return web.FileResponse(
        static_path,
        headers={
            "Content-Type": content_type,
            "Cache-Control": "public, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def proxy(request: web.Request) -> web.StreamResponse:
    if (
        request.path.startswith("/_alicedev/")
        or request.path.startswith("/t/")
        or request.path == "/internal/tokens"
    ):
        raise web.HTTPNotFound(text="网关路径不存在。\n")
    paseo: PaseoProxy = request.app["paseo_proxy"]
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return await paseo.websocket(request)
    return await paseo.http(request)


async def _fetch_bot_status(session: ClientSession, config: GatewayConfig) -> BotStatus:
    try:
        async with session.get(
            f"{config.bot_upstream}/v1/status",
            headers={
                "X-Alicedev-Token": config.internal_token,
                "Accept-Encoding": "identity",
            },
            timeout=ClientTimeout(total=5),
        ) as response:
            if response.status != 200:
                return BotStatus.unavailable(f"bot 返回 HTTP {response.status}")
            payload = await response.json()
    except (ClientError, asyncio.TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return BotStatus.unavailable(type(exc).__name__)
    return _parse_bot_status(payload)


def _parse_bot_status(payload: object) -> BotStatus:
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        return BotStatus.unavailable("bot 状态不可用")
    sessions = payload.get("sessions")
    session_map = sessions if isinstance(sessions, Mapping) else {}
    return BotStatus(
        ok=True,
        generation=_display_value(payload.get("generation")),
        uptime_s=_display_value(payload.get("uptime_s")),
        platforms=_string_values(payload.get("platforms")),
        commands=_command_values(payload.get("commands")),
        templates=_template_values(payload.get("templates")),
        active_sessions=_display_value(session_map.get("active")),
        closed_sessions=_display_value(session_map.get("closed")),
    )


def _command_values(value: object) -> tuple[BotCommand, ...]:
    if not isinstance(value, list):
        return ()
    result: list[BotCommand] = []
    for item in value:
        if isinstance(item, str):
            result.append(BotCommand(name=item, description=""))
        elif isinstance(item, Mapping):
            name = item.get("name")
            if isinstance(name, str) and name:
                description = item.get("description")
                result.append(
                    BotCommand(
                        name=name,
                        description=description if isinstance(description, str) else "",
                    )
                )
    return tuple(result)


def _template_values(value: object) -> tuple[BotTemplate, ...]:
    if not isinstance(value, list):
        return ()
    result: list[BotTemplate] = []
    for item in value:
        if isinstance(item, str):
            result.append(BotTemplate(name=item, description=""))
        elif isinstance(item, Mapping):
            name = item.get("name")
            if isinstance(name, str) and name:
                description = item.get("description")
                result.append(
                    BotTemplate(
                        name=name,
                        description=description if isinstance(description, str) else "",
                    )
                )
    return tuple(result)


def _string_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _display_value(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return escape(str(value))
    return "—"


def _render_status(status: BotStatus, target: str | None) -> str:
    state = "运行中" if status.ok else "降级（bot 不可用）"
    state_class = "ok" if status.ok else "degraded"
    error = f'<p class="error">{escape(status.error)}</p>' if status.error else ""
    platforms = "、".join(escape(item) for item in status.platforms) or "—"
    commands = _render_items(
        (item.name, item.description) for item in status.commands
    )
    templates = _render_items(
        (item.name, item.description) for item in status.templates
    )
    action = (
        f'<a class="button" href="{escape(target, quote=True)}">进入会话</a>'
        if target
        else '<span class="muted">没有可进入的会话目标</span>'
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>alicedev 状态</title>
  <style>
    :root {{ font-family: system-ui, sans-serif; color-scheme: light dark; }}
    body {{ max-width: 920px; margin: 0 auto; padding: 2rem 1.25rem; line-height: 1.55; }}
    .state {{ border-left: .35rem solid #12b76a; padding: .8rem 1rem; background: color-mix(in srgb, #12b76a 12%, transparent); }}
    .state.degraded {{ border-color: #f04438; background: color-mix(in srgb, #f04438 12%, transparent); }}
    .button {{ display: inline-block; padding: .6rem 1rem; border-radius: .4rem; background: #175cd3; color: white; text-decoration: none; }}
    .muted, .error {{ color: #667085; }}
    .error {{ color: #d92d20; }}
    code {{ font-family: ui-monospace, monospace; }}
  </style>
</head>
<body>
  <h1>alicedev</h1>
  <section class="state {state_class}"><strong>bot 状态：{state}</strong>{error}</section>
  <p>运行代数：<code>{status.generation}</code> · 运行时间：<code>{status.uptime_s}</code> 秒</p>
  <p>平台：{platforms} · 活跃会话：{status.active_sessions} · 已关闭：{status.closed_sessions}</p>
  <h2>可用指令</h2>
  {commands}
  <h2>Prompt 模板</h2>
  {templates}
  <p>{action}</p>
</body>
</html>
"""


def _render_token_preview(action: str) -> str:
    escaped_action = escape(action, quote=True)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>继续 alicedev</title>
</head>
<body>
  <h1>继续 alicedev</h1>
  <p>如果页面没有自动跳转，请点击下面的按钮。</p>
  <form id="redeem" method="post" action="{escaped_action}">
    <button type="submit">继续</button>
  </form>
  <script>{_PREVIEW_SCRIPT}</script>
</body>
</html>
"""



def _public_scheme(config: GatewayConfig) -> str:
    hostname = urlsplit(f"//{config.public_host}").hostname
    return "http" if hostname in {"localhost", "127.0.0.1", "::1"} else "https"
def _render_items(items: object) -> str:
    values = list(items)  # type: ignore[arg-type]
    if not values:
        return '<p class="muted">暂无</p>'
    rows = []
    for name, description in values:
        detail = f"：{escape(description)}" if description else ""
        rows.append(f"<li><code>{escape(name)}</code>{detail}</li>")
    return "<ul>" + "".join(rows) + "</ul>"


def _constant_time_equal(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


async def _read_json_object(request: web.Request) -> Mapping[str, object]:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise web.HTTPBadRequest(text="请求体必须是 JSON 对象。\n") from exc
    if not isinstance(payload, Mapping):
        raise web.HTTPBadRequest(text="请求体必须是 JSON 对象。\n")
    return payload
