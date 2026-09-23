"""Gateway tests against a fake Paseo upstream (aiohttp test server)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import gzip
from pathlib import Path
import sys
import zlib

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alicedev_gateway.app import create_app  # noqa: E402
from alicedev_gateway.config import GatewayConfig  # noqa: E402
from alicedev_gateway.proxy import (  # noqa: E402
    SHARE_VIEW_BOOT_SRC,
    SHARE_VIEW_STYLESHEET_HREF,
    decode_body,
    inject_share_view,
)
from alicedev_gateway.tokens import COOKIE_NAME, TokenTable  # noqa: E402

LINK = f'<link rel="stylesheet" href="{SHARE_VIEW_STYLESHEET_HREF}">'
BOOT = f'<script src="{SHARE_VIEW_BOOT_SRC}"></script>'
HTML = b"<!doctype html><html><head><title>Paseo</title></head><body>app</body></html>"
JS = b"console.log('bundle');" * 50


def _fake_upstream(seen: list[dict[str, str]]) -> web.Application:
    async def document(request: web.Request) -> web.Response:
        seen.append(dict(request.headers))
        mode = request.query.get("mode", "identity")
        if mode == "gzip":
            return web.Response(
                body=gzip.compress(HTML),
                headers={"Content-Type": "text/html; charset=utf-8", "Content-Encoding": "gzip", "ETag": '"x"'},
            )
        if mode == "deflate":
            return web.Response(
                body=zlib.compress(HTML),
                headers={"Content-Type": "text/html", "Content-Encoding": "deflate"},
            )
        if mode == "br":
            return web.Response(
                body=b"\x0b\x02\x80opaque-br",
                headers={"Content-Type": "text/html", "Content-Encoding": "br"},
            )
        return web.Response(body=HTML, headers={"Content-Type": "text/html; charset=utf-8", "ETag": '"x"'})

    async def script(request: web.Request) -> web.Response:
        seen.append(dict(request.headers))
        return web.Response(
            body=gzip.compress(JS),
            headers={"Content-Type": "application/javascript", "Content-Encoding": "gzip"},
        )

    app = web.Application()
    app.router.add_get("/h/srv/workspace/wks", document)
    app.router.add_get("/bundle.js", script)
    return app


async def _clients(tmp_path: Path) -> tuple[TestClient, TestServer, list[dict[str, str]]]:
    seen: list[dict[str, str]] = []
    upstream = TestServer(_fake_upstream(seen))
    await upstream.start_server()
    config = GatewayConfig(
        secret=b"test-secret",
        internal_token="internal",
        paseo_upstream=str(upstream.make_url("")).rstrip("/"),
        paseo_password="pw",
        bot_upstream="http://127.0.0.1:9",
        public_host="localhost",
        reports_published_root=tmp_path,
    )
    client = TestClient(TestServer(create_app(config)))
    await client.start_server()
    return client, upstream, seen


def _run(coro_fn, tmp_path: Path) -> None:
    async def main() -> None:
        client, upstream, seen = await _clients(tmp_path)
        try:
            await coro_fn(client, seen)
        finally:
            await client.close()
            await upstream.close()

    asyncio.run(main())


def _authorized(client: TestClient) -> dict[str, str]:
    codec = client.app["cookie_codec"]
    return {"Cookie": f"{COOKIE_NAME}={codec.issue(user_key='telegram:1')}"}
def _assert_themed_response(resp, body: str, *, status: int) -> None:
    assert resp.status == status
    assert resp.headers["Content-Type"].startswith("text/html; charset=utf-8")
    assert "style-src 'self'" in resp.headers["Content-Security-Policy"]
    assert "<style" not in body


def _preview_script_hash() -> str:
    script = 'document.getElementById("redeem").submit();'
    digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii")
    return f"sha256-{digest}"




# --- pure helpers -------------------------------------------------------------


def test_inject_boot_first_and_stylesheet_before_head_close() -> None:
    out = inject_share_view(HTML)
    assert out.count(LINK.encode()) == 1 and out.count(BOOT.encode()) == 1
    # The boot script must run before any Paseo script in <head>.
    assert out.startswith(b"<!doctype html><html><head>" + BOOT.encode())
    assert out.index(LINK.encode()) < out.index(b"</head>")


def test_inject_is_idempotent_and_handles_missing_head() -> None:
    once = inject_share_view(HTML)
    assert inject_share_view(once) == once
    assert inject_share_view(b"<HEAD lang=x><title>t</title>").startswith(
        b"<HEAD lang=x>" + LINK.encode() + BOOT.encode()
    )
    assert inject_share_view(b"<p>bare</p>") == LINK.encode() + BOOT.encode() + b"<p>bare</p>"


def test_decode_body_variants() -> None:
    assert decode_body(HTML, "identity") == HTML
    assert decode_body(gzip.compress(HTML), "gzip") == HTML
    assert decode_body(zlib.compress(HTML), "deflate") == HTML
    assert decode_body(HTML, "br") is None
    assert decode_body(b"not gzip", "gzip") is None


# --- token targets ---------------------------------------------------------------


def test_token_targets() -> None:
    ok = TokenTable.validate_target
    assert ok("/h/srv_1/workspace/wks_2?open=agent%3Aabc-123")
    assert ok("/h/srv_1/workspace/wks_2")
    assert not ok("/h/srv_1/workspace/wks_2?open=agent%3Aabc&embed=1")
    assert not ok("/h/srv_1/workspace/wks_2?embed=1")
    assert not ok("/settings")
    assert not ok("//evil.example/h/a/workspace/b")
    assert not ok("/h/a/workspace/b/../../x")


# --- routing & cookie gate -----------------------------------------------------------


def test_public_static_and_reports_without_cookie(tmp_path: Path) -> None:
    async def body(client: TestClient, seen: list[dict[str, str]]) -> None:
        css = await client.get("/_alicedev/static/paseo-view.css")
        assert css.status == 200
        assert css.headers["Content-Type"].startswith("text/css")
        assert "left-sidebar-resize-handle" in await css.text()
        boot = await client.get("/_alicedev/static/paseo-boot.js")
        assert boot.status == 200
        assert boot.headers["Content-Type"].startswith("application/javascript")
        assert "@paseo:daemon-registry" in await boot.text()

        alice_css = await client.get("/_alicedev/static/alice/alice.css")
        assert alice_css.status == 200
        assert alice_css.headers["Content-Type"] == "text/css; charset=utf-8"
        assert "--night: #0f0b12" in await alice_css.text()
        illustration = await client.get("/_alicedev/static/alice/alice-torn.webp")
        assert illustration.status == 200
        assert illustration.headers["Content-Type"] == "image/webp"
        font = await client.get("/_alicedev/static/alice/fell-sc.woff2")
        assert font.status == 200
        assert font.headers["Content-Type"] == "font/woff2"

        for path in (
            "/_alicedev/static/alice/nope.css",
            "/_alicedev/static/alice/../app.py",
        ):
            missing_static = await client.get(path)
            assert missing_static.status == 404
            assert missing_static.headers["Content-Type"].startswith("text/plain")
            assert "静态资源不存在" in await missing_static.text()

        report_id = "r_" + "a" * 26
        report_dir = tmp_path / report_id
        report_dir.mkdir()
        (report_dir / "readme.md").write_text("# Tea party\n\n```mermaid\nflowchart LR\nA-->B\n```\n")
        report = await client.get(f"/_alicedev/r/{report_id}/readme.md")
        report_body = await report.text()
        assert report.status == 200
        assert '<link rel="stylesheet" href="/_alicedev/static/alice/alice.css">' in report_body
        assert 'class="report-frame__header"' in report_body
        assert '<style>' in report_body
        assert "mermaid.initialize" in report_body

        missing_report = await client.get("/_alicedev/r/r_aaaaaaaaaaaaaaaaaaaaaaaaaa/x.md")
        missing_report_body = await missing_report.text()
        _assert_themed_response(missing_report, missing_report_body, status=404)
        assert "报告文件不存在" in missing_report_body
        bad_shape = await client.get("/_alicedev/r/whatever")
        bad_shape_body = await bad_shape.text()
        _assert_themed_response(bad_shape, bad_shape_body, status=404)
        assert "网关路径不存在" in bad_shape_body

        post = await client.post("/_alicedev/static/paseo-view.css")
        assert post.status == 405
        status_page = await client.get("/_alicedev/")
        status_body = await status_page.text()
        _assert_themed_response(status_page, status_body, status=403)
        assert "需要有效的 alicedev 分享授权" in status_body
        paseo = await client.get("/h/srv/workspace/wks", headers={"Accept": "text/html"})
        paseo_body = await paseo.text()
        _assert_themed_response(paseo, paseo_body, status=403)
        assert "需要有效的 alicedev 分享授权" in paseo_body
        assert seen == []

    _run(body, tmp_path)


def test_status_page_accepts_bare_workspace_target(tmp_path: Path) -> None:
    async def body(client: TestClient, seen: list[dict[str, str]]) -> None:
        resp = await client.get(
            "/_alicedev/?go=%2Fh%2Fsrv%2Fworkspace%2Fwks", headers=_authorized(client)
        )
        text = await resp.text()
        _assert_themed_response(resp, text, status=200)
        assert 'href="/h/srv/workspace/wks"' in text
        assert "降级（bot 不可用）" in text

    _run(body, tmp_path)
def test_token_preview_and_consume_are_one_time(tmp_path: Path) -> None:
    async def body(client: TestClient, seen: list[dict[str, str]]) -> None:
        del seen
        issued = await client.post(
            "/internal/tokens",
            headers={"X-Alicedev-Token": "internal"},
            json={
                "target": "/h/srv/workspace/wks",
                "user_key": "telegram:1",
            },
        )
        assert issued.status == 200
        token = (await issued.json())["token"]

        preview = await client.get(f"/t/{token}")
        preview_body = await preview.text()
        _assert_themed_response(preview, preview_body, status=200)
        assert '<form id="redeem" method="post" action="/t/' in preview_body
        assert 'document.getElementById("redeem").submit();' in preview_body
        assert _preview_script_hash() in preview.headers["Content-Security-Policy"]

        first_post = await client.post(f"/t/{token}", allow_redirects=False)
        assert first_post.status == 303
        assert first_post.headers["Location"].startswith("/_alicedev/?go=")
        assert "Set-Cookie" in first_post.headers

        second_post = await client.post(f"/t/{token}", allow_redirects=False)
        second_body = await second_post.text()
        _assert_themed_response(second_post, second_body, status=403)
        assert "链接无效、已使用或已过期" in second_body

    _run(body, tmp_path)




# --- share-view injection ---------------------------------------------------------------


def test_html_navigation_is_injected_and_requests_identity(tmp_path: Path) -> None:
    async def body(client: TestClient, seen: list[dict[str, str]]) -> None:
        resp = await client.get(
            "/h/srv/workspace/wks",
            headers={**_authorized(client), "Accept": "text/html,*/*", "Accept-Encoding": "gzip, br"},
        )
        raw = await resp.read()
        assert resp.status == 200
        assert LINK.encode() in raw
        assert raw.index(LINK.encode()) < raw.index(b"</head>")
        assert raw.index(BOOT.encode()) < raw.index(b"<title>")
        assert "Content-Encoding" not in resp.headers
        assert "ETag" not in resp.headers
        assert int(resp.headers["Content-Length"]) == len(raw)
        assert seen[-1]["Accept-Encoding"] == "identity"

    _run(body, tmp_path)


def test_compressed_html_from_upstream_is_decoded(tmp_path: Path) -> None:
    async def body(client: TestClient, seen: list[dict[str, str]]) -> None:
        for mode in ("gzip", "deflate"):
            resp = await client.get(
                f"/h/srv/workspace/wks?mode={mode}",
                headers={**_authorized(client), "Accept": "text/html"},
                auto_decompress=False,
            )
            raw = await resp.read()
            assert resp.status == 200, mode
            assert "Content-Encoding" not in resp.headers, mode
            assert LINK.encode() in raw, mode

    _run(body, tmp_path)


def test_undecodable_html_passes_through_unchanged(tmp_path: Path) -> None:
    async def body(client: TestClient, seen: list[dict[str, str]]) -> None:
        resp = await client.get(
            "/h/srv/workspace/wks?mode=br",
            headers={**_authorized(client), "Accept": "text/html"},
            auto_decompress=False,
        )
        raw = await resp.read()
        assert resp.status == 200
        assert resp.headers["Content-Encoding"] == "br"
        assert raw == b"\x0b\x02\x80opaque-br"

    _run(body, tmp_path)


def test_non_html_assets_stream_untouched(tmp_path: Path) -> None:
    async def body(client: TestClient, seen: list[dict[str, str]]) -> None:
        resp = await client.get(
            "/bundle.js",
            headers={**_authorized(client), "Accept": "*/*", "Accept-Encoding": "gzip"},
            auto_decompress=False,
        )
        raw = await resp.read()
        assert resp.status == 200
        assert resp.headers["Content-Encoding"] == "gzip"
        assert gzip.decompress(raw) == JS
        assert seen[-1]["Accept-Encoding"] == "gzip"

    _run(body, tmp_path)


def test_self_hosted_prefix_and_head_are_not_injected(tmp_path: Path) -> None:
    async def body(client: TestClient, seen: list[dict[str, str]]) -> None:
        resp = await client.head(
            "/h/srv/workspace/wks", headers={**_authorized(client), "Accept": "text/html"}
        )
        assert resp.status == 200
        assert "Accept-Encoding" not in seen[-1] or seen[-1]["Accept-Encoding"] != "identity"

    _run(body, tmp_path)


def test_paseo_down_html_and_non_html(tmp_path: Path) -> None:
    async def body() -> None:
        config = GatewayConfig(
            secret=b"test-secret",
            internal_token="internal",
            paseo_upstream="http://127.0.0.1:9",
            paseo_password="pw",
            bot_upstream="http://127.0.0.1:9",
            public_host="localhost",
            reports_published_root=tmp_path,
        )
        client = TestClient(TestServer(create_app(config)))
        await client.start_server()
        try:
            html_resp = await client.get(
                "/h/srv/workspace/wks",
                headers={**_authorized(client), "Accept": "text/html"},
            )
            html_body = await html_resp.text()
            _assert_themed_response(html_resp, html_body, status=502)
            assert "paseo 上游暂时不可用" in html_body

            js_resp = await client.get(
                "/h/srv/workspace/wks",
                headers={**_authorized(client), "Accept": "application/javascript"},
            )
            js_body = await js_resp.text()
            assert js_resp.status == 502
            assert not js_resp.headers["Content-Type"].startswith("text/html")
            assert "paseo 上游暂时不可用" in js_body
        finally:
            await client.close()

    asyncio.run(body())


def test_unreadable_report_returns_themed_500(tmp_path: Path) -> None:
    async def body(client: TestClient, seen: list[dict[str, str]]) -> None:
        del seen
        report_id = "r_" + "b" * 26
        report_dir = tmp_path / report_id
        report_dir.mkdir()
        (report_dir / "bad.md").write_bytes(b"\xff\xfe\x00invalid-utf8")
        resp = await client.get(f"/_alicedev/r/{report_id}/bad.md")
        body_text = await resp.text()
        _assert_themed_response(resp, body_text, status=500)
        assert "报告读取失败" in body_text

    _run(body, tmp_path)
