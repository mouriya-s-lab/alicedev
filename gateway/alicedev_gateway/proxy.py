"""Paseo reverse proxy with separate browser and upstream WS negotiations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
import logging
import re
from typing import Final
import zlib

from aiohttp import ClientError, ClientResponse, ClientSession, WSMsgType, web

from .config import GatewayConfig


_LOGGER = logging.getLogger("alicedev_gateway.proxy")

# Share view (ARCHITECTURE §8): every proxied Paseo HTML document gets two
# gateway-owned assets; Paseo's source is never modified.  The boot script goes
# first in <head> so it runs before Paseo's deferred bundle (clean self-hosted
# boot, see static/paseo-boot.js); the stylesheet only hides navigation chrome.
SHARE_VIEW_STYLESHEET_HREF: Final[str] = "/_alicedev/static/paseo-view.css"
SHARE_VIEW_BOOT_SRC: Final[str] = "/_alicedev/static/paseo-boot.js"
_SHARE_VIEW_LINK: Final[bytes] = (
    f'<link rel="stylesheet" href="{SHARE_VIEW_STYLESHEET_HREF}">'.encode("ascii")
)
_SHARE_VIEW_BOOT: Final[bytes] = f'<script src="{SHARE_VIEW_BOOT_SRC}"></script>'.encode("ascii")
# Largest HTML document the gateway buffers for injection.  Paseo's index.html
# is a small SPA shell; anything larger streams through untouched.
_MAX_INJECT_BYTES: Final[int] = 4 * 1024 * 1024
_HEAD_CLOSE = re.compile(rb"</head\s*>", re.IGNORECASE)
_HEAD_OPEN = re.compile(rb"<head(\s[^>]*)?>", re.IGNORECASE)
# Headers that no longer describe the body after injection.
_INJECTED_DROP_HEADERS: Final[frozenset[str]] = frozenset(
    {"content-length", "content-encoding", "etag", "content-md5"}
)


_HOP_BY_HOP_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_REQUEST_AUTH_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "cookie",
        "host",
        "content-length",
        "sec-websocket-accept",
        "sec-websocket-extensions",
        "sec-websocket-key",
        "sec-websocket-protocol",
        "sec-websocket-version",
    }
)
_RESPONSE_DROP_HEADERS: Final[frozenset[str]] = _HOP_BY_HOP_HEADERS | frozenset(
    {"set-cookie"}
)

# The Paseo web bundle is built self-hosted (EXPO_PUBLIC_PASEO_SELFHOSTED=true):
# it reads /_paseo/hosts.json and dials `<basePath>/ws` on the page origin.
# The gateway advertises one managed daemon and strips its prefix upstream.
SELF_HOSTED_DAEMON_ID: Final[str] = "alicedev"
SELF_HOSTED_BASE_PATH: Final[str] = f"/daemons/{SELF_HOSTED_DAEMON_ID}"
SELF_HOSTED_MANIFEST: Final[tuple[dict[str, str], ...]] = (
    {"id": SELF_HOSTED_DAEMON_ID, "label": "alicedev", "basePath": SELF_HOSTED_BASE_PATH},
)


@dataclass(slots=True)
class PaseoProxy:
    config: GatewayConfig
    session: ClientSession

    def upstream_url(self, request: web.Request) -> str:
        rel = str(request.rel_url)
        if rel == SELF_HOSTED_BASE_PATH or rel.startswith((f"{SELF_HOSTED_BASE_PATH}/", f"{SELF_HOSTED_BASE_PATH}?")):
            rel = rel[len(SELF_HOSTED_BASE_PATH):]
            if not rel.startswith("/"):
                rel = f"/{rel}"
        return f"{self.config.paseo_upstream}{rel}"

    async def http(self, request: web.Request) -> web.StreamResponse:
        headers = self._http_request_headers(request)
        html_navigation = _is_html_navigation(request)
        if html_navigation:
            # Ask upstream for an uncompressed document so the stylesheet can be
            # injected without re-encoding; Caddy compresses toward the browser.
            headers["Accept-Encoding"] = "identity"
        body: AsyncIterator[bytes] | None = None
        if request.can_read_body and request.method not in {"GET", "HEAD"}:
            body = request.content.iter_chunked(64 * 1024)

        try:
            upstream = await self.session.request(
                request.method,
                self.upstream_url(request),
                headers=headers,
                data=body,
                allow_redirects=False,
            )
        except (ClientError, asyncio.TimeoutError) as exc:
            raise web.HTTPBadGateway(text="paseo 上游暂时不可用") from exc

        if html_navigation and _is_injectable_html(upstream):
            return await self._html_with_share_view(request, upstream)
        return await self._stream(request, upstream)

    async def _html_with_share_view(
        self, request: web.Request, upstream: ClientResponse
    ) -> web.StreamResponse:
        """Buffer an HTML document, inject the share-view stylesheet, respond.

        An oversized document streams through untouched (prefix already read is
        replayed first); an encoding the gateway cannot decode (e.g. ``br``)
        is returned byte-for-byte with its original headers.
        """
        try:
            buffered = bytearray()
            async for chunk in upstream.content.iter_chunked(64 * 1024):
                buffered += chunk
                if len(buffered) > _MAX_INJECT_BYTES:
                    _LOGGER.warning("html document too large to inject share view; streaming as-is")
                    return await self._stream(request, upstream, prefix=bytes(buffered))

            headers = self._response_headers(upstream.headers)
            encoding = upstream.headers.get("Content-Encoding", "identity")
            decoded = decode_body(bytes(buffered), encoding)
            if decoded is None:
                _LOGGER.warning(
                    "cannot decode upstream html encoding %r; share view not injected", encoding
                )
                return web.Response(
                    status=upstream.status,
                    reason=upstream.reason,
                    body=bytes(buffered),
                    headers={k: v for k, v in headers.items() if k.lower() != "content-length"},
                )
            return web.Response(
                status=upstream.status,
                reason=upstream.reason,
                body=inject_share_view(decoded),
                headers={
                    k: v for k, v in headers.items() if k.lower() not in _INJECTED_DROP_HEADERS
                },
            )
        finally:
            upstream.close()

    async def _stream(
        self,
        request: web.Request,
        upstream: ClientResponse,
        *,
        prefix: bytes = b"",
    ) -> web.StreamResponse:
        response = web.StreamResponse(
            status=upstream.status,
            reason=upstream.reason,
            headers=self._response_headers(upstream.headers),
        )
        try:
            await response.prepare(request)
            if prefix:
                await response.write(prefix)
            async for chunk in upstream.content.iter_chunked(64 * 1024):
                await response.write(chunk)
            await response.write_eof()
            return response
        finally:
            upstream.close()

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        upstream_protocol = f"paseo.bearer.{self.config.paseo_password}"
        headers = {
            "Authorization": f"Bearer {self.config.paseo_password}",
            "Host": self.config.public_host,
            "Origin": f"https://{self.config.public_host}",
        }
        try:
            upstream = await self.session.ws_connect(
                self.upstream_url(request),
                headers=headers,
                protocols=(upstream_protocol,),
                autoping=True,
                autoclose=True,
            )
        except (ClientError, asyncio.TimeoutError) as exc:
            raise web.HTTPBadGateway(text="paseo WebSocket 上游暂时不可用") from exc

        # Do not provide `protocols` here.  aiohttp therefore does not echo the
        # browser's offered Sec-WebSocket-Protocol or Paseo's bearer protocol.
        browser = web.WebSocketResponse(autoping=True, autoclose=True)
        try:
            await browser.prepare(request)
        except BaseException:
            await upstream.close()
            raise

        async def browser_to_upstream() -> None:
            async for message in browser:
                if message.type is WSMsgType.TEXT:
                    await upstream.send_str(message.data)
                elif message.type is WSMsgType.BINARY:
                    await upstream.send_bytes(message.data)
                elif message.type is WSMsgType.CLOSE:
                    await upstream.close(code=message.data or 1000, message=message.extra)
                    return
                elif message.type is WSMsgType.ERROR:
                    return

        async def upstream_to_browser() -> None:
            async for message in upstream:
                if message.type is WSMsgType.TEXT:
                    await browser.send_str(message.data)
                elif message.type is WSMsgType.BINARY:
                    await browser.send_bytes(message.data)
                elif message.type is WSMsgType.CLOSE:
                    await browser.close(code=message.data or 1000, message=message.extra)
                    return
                elif message.type is WSMsgType.ERROR:
                    return

        client_task = asyncio.create_task(browser_to_upstream())
        server_task = asyncio.create_task(upstream_to_browser())
        try:
            _done, pending = await asyncio.wait(
                (client_task, server_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.gather(*_done, return_exceptions=True)
        finally:
            for task in (client_task, server_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(client_task, server_task, return_exceptions=True)
            with suppress(Exception):
                await upstream.close()
            with suppress(Exception):
                await browser.close()
        return browser

    def _http_request_headers(self, request: web.Request) -> dict[str, str]:
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in _HOP_BY_HOP_HEADERS
            and key.lower() not in _REQUEST_AUTH_HEADERS
        }
        headers["Authorization"] = f"Bearer {self.config.paseo_password}"
        headers["Host"] = self.config.public_host
        return headers

    @staticmethod
    def _response_headers(headers: object) -> dict[str, str]:
        source = headers.items()  # type: ignore[attr-defined]
        return {
            key: value
            for key, value in source
            if key.lower() not in _RESPONSE_DROP_HEADERS
        }


def _is_html_navigation(request: web.Request) -> bool:
    """A browser document load: GET that accepts HTML and is not a WS upgrade."""
    return request.method == "GET" and "text/html" in request.headers.get("Accept", "")


def _is_injectable_html(upstream: ClientResponse) -> bool:
    content_type = upstream.headers.get("Content-Type", "")
    return upstream.status == 200 and content_type.split(";", 1)[0].strip().lower() == "text/html"


def decode_body(body: bytes, content_encoding: str) -> bytes | None:
    """Decode a response body; ``None`` when the encoding is not supported."""
    encodings = [e.strip().lower() for e in content_encoding.split(",") if e.strip()]
    data = body
    # Content-Encoding lists codings in application order; undo them in reverse.
    for encoding in reversed(encodings):
        match encoding:
            case "identity":
                continue
            case "gzip" | "x-gzip":
                try:
                    data = zlib.decompress(data, wbits=zlib.MAX_WBITS | 16)
                except zlib.error:
                    return None
            case "deflate":
                try:
                    data = zlib.decompress(data)
                except zlib.error:
                    try:
                        data = zlib.decompress(data, wbits=-zlib.MAX_WBITS)
                    except zlib.error:
                        return None
            case _:
                return None
    return data


def inject_share_view(html: bytes) -> bytes:
    """Insert the share-view boot script and stylesheet into an HTML document (idempotent)."""
    if SHARE_VIEW_BOOT_SRC.encode("ascii") not in html:
        open_tag = _HEAD_OPEN.search(html)
        at = open_tag.end() if open_tag is not None else 0
        html = html[:at] + _SHARE_VIEW_BOOT + html[at:]
    if SHARE_VIEW_STYLESHEET_HREF.encode("ascii") not in html:
        close = _HEAD_CLOSE.search(html)
        if close is not None:
            html = html[: close.start()] + _SHARE_VIEW_LINK + html[close.start():]
        else:
            open_tag = _HEAD_OPEN.search(html)
            at = open_tag.end() if open_tag is not None else 0
            html = html[:at] + _SHARE_VIEW_LINK + html[at:]
    return html
