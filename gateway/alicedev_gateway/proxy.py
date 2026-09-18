"""Paseo reverse proxy with separate browser and upstream WS negotiations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from typing import Final

from aiohttp import ClientError, ClientSession, WSMsgType, web

from .config import GatewayConfig


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


@dataclass(slots=True)
class PaseoProxy:
    config: GatewayConfig
    session: ClientSession

    def upstream_url(self, request: web.Request) -> str:
        return f"{self.config.paseo_upstream}{request.rel_url}"

    async def http(self, request: web.Request) -> web.StreamResponse:
        headers = self._http_request_headers(request)
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

        response = web.StreamResponse(
            status=upstream.status,
            reason=upstream.reason,
            headers=self._response_headers(upstream.headers),
        )
        try:
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(64 * 1024):
                await response.write(chunk)
            await response.write_eof()
            return response
        except (ConnectionError, asyncio.CancelledError):
            raise
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
