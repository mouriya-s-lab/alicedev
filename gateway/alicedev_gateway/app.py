"""aiohttp application for token-gated Paseo and public reports."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import time
from typing import Mapping
from urllib.parse import quote, urlsplit
from aiohttp import ClientError, ClientSession, ClientTimeout, web

from .config import GatewayConfig
from .pages import (
    CONFIRM_HEADERS,
    ErrorPageKind,
    SessionDetail,
    THEMED_HEADERS,
    render_error_page,
    render_session_page,
    render_token_preview,
)
from .proxy import SELF_HOSTED_MANIFEST, PaseoProxy
from .reports import ReportRenderer, resolve_report_asset
from .tokens import COOKIE_NAME, COOKIE_MAX_AGE, SignedCookieCodec, TokenTable


_LOGGER = logging.getLogger("alicedev_gateway.access")
_SWEEP_INTERVAL_SECONDS = 60.0

# This is intentionally an exact path -> MIME manifest. Request paths are
# looked up before any filesystem access, so an unlisted path cannot become a
# static file fallback or traversal primitive.
STATIC_MANIFEST: dict[str, str] = {
    "mermaid.min.js": "application/javascript; charset=utf-8",
    "pygments.css": "text/css; charset=utf-8",
    "paseo-view.css": "text/css; charset=utf-8",
    "paseo-boot.js": "application/javascript; charset=utf-8",
    "alice/alice.css": "text/css; charset=utf-8",
    "alice/alice-hero.webp": "image/webp",
    "alice/alice-torn.webp": "image/webp",
    "alice/alice-emblem.webp": "image/webp",
    "alice/damask.webp": "image/webp",
    "alice/alice-icon.png": "image/png",
    "alice/fell-sc.woff2": "font/woff2",
    "alice/fell-italic.woff2": "font/woff2",
    "alice/OFL-IMFell.txt": "text/plain; charset=utf-8",
}
_PUBLIC_GET_PREFIXES = ("/_alicedev/r/", "/_alicedev/static/")
_PREVIEW_HEADERS = {
    **CONFIRM_HEADERS,
    "Cache-Control": "no-store",
}


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
    app.router.add_get("/_alicedev/", session_page)
    app.router.add_get("/_alicedev/r/{report_id}/{basename}", report)
    app.router.add_get("/_alicedev/static/{asset:.+}", static_asset)
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
    if path == "/_alicedev/health":
        if request.method not in {"GET", "HEAD"}:
            raise web.HTTPMethodNotAllowed(request.method, {"GET", "HEAD"})
        return await handler(request)
    if path == "/internal/tokens":
        if request.method != "POST":
            raise web.HTTPMethodNotAllowed(request.method, {"POST"})
        return await handler(request)
    if path.startswith("/t/"):
        if request.method not in {"GET", "HEAD", "POST"}:
            raise web.HTTPMethodNotAllowed(request.method, {"GET", "HEAD", "POST"})
        return await handler(request)
    if path.startswith(_PUBLIC_GET_PREFIXES):
        if request.method not in {"GET", "HEAD"}:
            raise web.HTTPMethodNotAllowed(request.method, {"GET", "HEAD"})
        return await handler(request)

    codec: SignedCookieCodec = request.app["cookie_codec"]
    if not codec.verify(request.cookies.get(COOKIE_NAME)):
        return _themed_response(
            403,
            render_error_page(ErrorPageKind.NEEDS_INVITATION),
        )
    return await handler(request)


def _themed_response(status: int, text: str) -> web.Response:
    return web.Response(
        status=status,
        text=text,
        content_type="text/html",
        charset="utf-8",
        headers=THEMED_HEADERS,
    )

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
    session_id = payload.get("session_id")
    ttl_value = payload.get("ttl_s")
    if not isinstance(target, str) or not isinstance(user_key, str):
        raise web.HTTPBadRequest(text="target 与 user_key 必须是字符串。\n")
    if isinstance(session_id, bool) or not isinstance(session_id, int):
        raise web.HTTPBadRequest(text="session_id 必须是整数。\n")

    ttl_s: int | float | None
    if ttl_value is None:
        ttl_s = None
    elif isinstance(ttl_value, (int, float)) and not isinstance(ttl_value, bool):
        ttl_s = ttl_value
    else:
        raise web.HTTPBadRequest(text="ttl_s 必须是正数。\n")

    tokens: TokenTable = request.app["token_table"]
    try:
        issued = tokens.issue(target=target, user_key=user_key, session_id=session_id, ttl_s=ttl_s)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=f"{exc}\n") from exc

    url = f"{_public_scheme(config)}://{config.public_host}/t/{issued.token}"
    return web.json_response({"token": issued.token, "url": url})


async def preview_token(request: web.Request) -> web.Response:
    token = request.match_info["token"]
    tokens: TokenTable = request.app["token_table"]
    if not tokens.validate_token(token) or tokens.peek(token) is None:
        return _themed_response(
            403,
            render_error_page(ErrorPageKind.LINK_SPENT),
        )
    return web.Response(
        text=render_token_preview(request.path),
        content_type="text/html",
        charset="utf-8",
        headers=_PREVIEW_HEADERS,
    )


async def redeem_token(request: web.Request) -> web.StreamResponse:
    token = request.match_info["token"]
    tokens: TokenTable = request.app["token_table"]
    record = tokens.consume(token)
    if record is None:
        return _themed_response(
            403,
            render_error_page(ErrorPageKind.LINK_SPENT),
        )

    codec: SignedCookieCodec = request.app["cookie_codec"]
    response = web.HTTPSeeOther(
        location=f"/_alicedev/?session={record.session_id}&go={quote(record.target, safe='')}"
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


async def session_page(request: web.Request) -> web.Response:
    raw_session = request.query.get("session", "")
    target = request.query.get("go", "")
    if not raw_session.isdecimal() or not TokenTable.validate_target(target):
        return _themed_response(404, render_error_page(ErrorPageKind.PATH_MISSING))
    config: GatewayConfig = request.app["gateway_config"]
    session: ClientSession = request.app["http_session"]
    detail = await _fetch_session(session, config, int(raw_session))
    return _themed_response(200, render_session_page(detail, target))


async def report(request: web.Request) -> web.StreamResponse:
    config: GatewayConfig = request.app["gateway_config"]
    asset = resolve_report_asset(
        config.reports_published_root,
        request.match_info["report_id"],
        request.match_info["basename"],
    )
    if asset is None:
        return _themed_response(
            404,
            render_error_page(ErrorPageKind.REPORT_MISSING),
        )

    headers = {
        "X-Robots-Tag": "noindex",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }
    if asset.path.suffix.lower() == ".md":
        try:
            source = asset.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return _themed_response(
                500,
                render_error_page(ErrorPageKind.REPORT_UNREADABLE),
            )
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
    content_type = STATIC_MANIFEST.get(asset_name)
    if content_type is None:
        raise web.HTTPNotFound(text="静态资源不存在。\n")
    static_path = Path(__file__).resolve().parent.parent / "static" / asset_name
    if not static_path.is_file():
        raise web.HTTPNotFound(text="静态资源不存在。\n")
    return web.FileResponse(
        static_path,
        headers={
            "Content-Type": content_type,
            "Cache-Control": "public, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def proxy(request: web.Request) -> web.StreamResponse:
    # Unmatched /_alicedev/ paths (e.g. /_alicedev/r/<bad shape>) must not reach
    # paseo; the cookie gate already lets the public prefixes through.
    if (
        request.path.startswith("/_alicedev/")
        or request.path.startswith("/t/")
        or request.path == "/internal/tokens"
    ):
        return _themed_response(
            404,
            render_error_page(ErrorPageKind.PATH_MISSING),
        )
    paseo: PaseoProxy = request.app["paseo_proxy"]
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return await paseo.websocket(request)
    return await paseo.http(request)


async def _fetch_session(
    session: ClientSession, config: GatewayConfig, session_id: int
) -> SessionDetail | None:
    """Bot ``GET /v1/sessions/{id}``; any failure means the page shows no details."""
    try:
        async with session.get(
            f"{config.bot_upstream}/v1/sessions/{session_id}",
            headers={"X-Alicedev-Token": config.internal_token, "Accept-Encoding": "identity"},
            timeout=ClientTimeout(total=5),
        ) as response:
            if response.status != 200:
                _LOGGER.warning("bot session %s lookup returned HTTP %s", session_id, response.status)
                return None
            payload = await response.json()
    except (ClientError, asyncio.TimeoutError, json.JSONDecodeError, ValueError) as exc:
        _LOGGER.warning("bot session %s lookup failed: %r", session_id, exc)
        return None
    detail = _parse_session(payload)
    if detail is None:
        _LOGGER.warning("bot session %s lookup returned an unexpected body", session_id)
    return detail


def _parse_session(payload: object) -> SessionDetail | None:
    if not isinstance(payload, Mapping):
        return None
    scenario = payload.get("scenario")
    state = payload.get("state")
    if not isinstance(scenario, Mapping) or not isinstance(state, Mapping):
        return None
    strings = {
        "label": payload.get("label"),
        "name": payload.get("name"),
        "scenario_name": scenario.get("name"),
        "scenario_description": scenario.get("description"),
        "state_label": state.get("label"),
        "created_by": payload.get("created_by"),
        "created_at": payload.get("created_at"),
    }
    last_activity_at = payload.get("last_activity_at")
    is_current = payload.get("is_current")
    if (
        not all(isinstance(value, str) for value in strings.values())
        or not (last_activity_at is None or isinstance(last_activity_at, str))
        or not isinstance(is_current, bool)
    ):
        return None
    return SessionDetail(**strings, last_activity_at=last_activity_at, is_current=is_current)



def _public_scheme(config: GatewayConfig) -> str:
    hostname = urlsplit(f"//{config.public_host}").hostname
    return "http" if hostname in {"localhost", "127.0.0.1", "::1"} else "https"


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
