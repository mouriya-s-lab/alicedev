"""CLI bridge for t2i upgrade evidence cards inside an AstrBot container.

The paseo conductor can invoke this through ``docker exec``.  The bridge uses
``CardRenderer`` (the same renderer used by bot replies), but supplies a tiny
HTTP host that talks to the container-local t2i service.  It prints both a
shared-path reference and a data URI so callers can choose the transport that
matches their isolated volume topology.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from alicedev.images import image_data_uri
from alicedev.render.cards import CardRenderer


class T2IHttpHost:
    """Minimal ``HtmlRenderHost`` backed by AstrBot's t2i HTTP API."""

    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint.rstrip("/") + "/text2img/generate"

    async def html_render(
        self,
        tmpl: str,
        data: dict[str, Any],
        *,
        return_url: bool = True,
        options: dict[str, Any] | None = None,
    ) -> str:
        del data, return_url
        request_body = json.dumps(
            {"html": tmpl, "json": False, "options": options or {}},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self._endpoint,
            data=request_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = await asyncio.to_thread(urllib.request.urlopen, request, timeout=60)
            try:
                image_bytes = await asyncio.to_thread(response.read)
            finally:
                response.close()
        except (OSError, urllib.error.URLError) as exc:
            raise RuntimeError(f"t2i render failed: {exc}") from exc
        if not image_bytes:
            raise RuntimeError("t2i returned an empty image")
        handle, raw_path = tempfile.mkstemp(prefix="alicedev-card-", suffix=".png")
        path = Path(raw_path)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(image_bytes)
        except OSError:
            path.unlink(missing_ok=True)
            raise
        return str(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rendercard")
    parser.add_argument("kind", choices=("explanation", "evidence"))
    parser.add_argument(
        "--json",
        dest="json_path",
        required=True,
        metavar="<input.json>",
        help="structured summary JSON, or '-' to read JSON from stdin",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional PNG destination; otherwise a temporary path is returned",
    )
    parser.add_argument(
        "--templates-root",
        type=Path,
        default=Path(os.environ.get("ALICEDEV_TEMPLATES_ROOT", "/AstrBot/alicedev-templates")),
    )
    parser.add_argument(
        "--t2i-url",
        default=os.environ.get("T2I_URL", "http://t2i:8999"),
    )
    return parser


def _load_json(raw_path: str) -> dict[str, Any]:
    raw = sys.stdin.read() if raw_path == "-" else Path(raw_path).read_text(encoding="utf-8")
    value: Any = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("input JSON must be an object")
    return value


def _output_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    destination = path.expanduser()
    if destination.suffix.lower() != ".png":
        raise ValueError("--output must end in .png")
    return destination


async def _render(args: argparse.Namespace, payload: dict[str, Any]) -> Path:
    renderer = CardRenderer(T2IHttpHost(args.t2i_url), args.templates_root)
    output = _output_path(args.output)
    if args.kind == "explanation":
        return await renderer.render_upgrade_explanation(payload, output_path=output)

    # e2e_driver.py calls the two sides ``baseline``/``candidate``; callers
    # may also provide the renderer-friendly ``before``/``after`` names.
    before = payload.get("before") or payload.get("baseline")
    after = payload.get("after") or payload.get("candidate")
    if before is None or after is None:
        raise ValueError("evidence input requires before/after or baseline/candidate captures")
    before_fields = before if isinstance(before, dict) else {}
    after_fields = after if isinstance(after, dict) else {}
    same_scenario = payload.get("same_scenario")
    scenario_fields = same_scenario if isinstance(same_scenario, dict) else {}
    common = {
        "run_id": payload.get("run_id") or before_fields.get("run_id") or "",
        "baseline_sha": payload.get("baseline_sha") or before_fields.get("sha") or "",
        "candidate_sha": payload.get("candidate_sha") or after_fields.get("sha") or "",
        "scenario": (
            payload.get("scenario")
            or before_fields.get("scenario_id")
            or scenario_fields.get("surface")
            or scenario_fields.get("command")
            or ""
        ),
        "captured_at": payload.get("captured_at") or after_fields.get("capture_time") or "",
    }
    return await renderer.render_upgrade_evidence(
        before,
        after,
        output_path=output,
        **common,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = _load_json(args.json_path)
        path = asyncio.run(_render(args, payload))
        print(json.dumps({"path": str(path), "data_uri": image_data_uri(path)}))
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"rendercard: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
