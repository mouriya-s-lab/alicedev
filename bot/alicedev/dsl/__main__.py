"""``python -m alicedev.dsl`` — offline DSL tools (ARCHITECTURE §3.5).

* ``check [root]``            validate ``templates/``; exit 1 on any rejected file.
* ``card <name> [--out png]`` render the card of a command (or a scenario's flow
  card) through t2i; ``--html`` writes the HTML instead (no t2i needed).
* ``render <card> --fields <json|-> [--out png]`` render any ``templates/cards``
  card with the given fields (used by /升级bot for 说明图/证据图). String fields
  whose value is an existing local PNG/JPEG path are inlined as data URIs.

Run from ``bot/`` (so ``alicedev`` is importable) or with ``PYTHONPATH=bot``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import jinja2

from alicedev.dsl.loader import load_registry
from alicedev.dsl.model import Registry
from alicedev.render import views

def _default_root() -> Path:
    env = os.environ.get("ALICEDEV_TEMPLATES_ROOT")
    if env:
        return Path(env)
    container = Path("/AstrBot/alicedev-templates")
    if container.is_dir():
        return container
    return Path(__file__).resolve().parents[3] / "templates"


_DEFAULT_ROOT = _default_root()
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")


def _cmd_check(args: argparse.Namespace) -> int:
    reg = load_registry(args.root)
    for error in reg.errors:
        print(f"{error.path}:{error.line}: {error.message}")
    commands = len(reg.command_list())
    print(
        f"{'FAIL' if reg.errors else 'OK'}: {commands} 条指令, {len(reg.routes)} 条路由, "
        f"{len(reg.scenarios)} 个场景, {len(reg.messages)} 条回话, {len(reg.errors)} 个错误",
        file=sys.stderr,
    )
    return 1 if reg.errors else 0


def _card_for(reg: Registry, name: str) -> views.Card:
    command = reg.commands.get(name)
    if command is not None:
        return views.command_card(command, views.command_scenario(reg, command))
    scenario = reg.scenarios.get(name)
    if scenario is not None:
        return "command", {
            "title": f"场景 {scenario.name}",
            "subtitle": scenario.description,
            "aliases": [],
            "main": {
                "usage": f"场景 {scenario.name}",
                "summary": scenario.description,
                "type": "场景",
                "permission": "—",
                "params": [],
                "examples": [],
            },
            "subcommands": [],
            "flow": views.scenario_flow(scenario),
        }
    raise KeyError(name)


def _html(root: Path, card: str, fields: dict[str, Any]) -> str:
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(root / "cards")),
        autoescape=jinja2.select_autoescape(("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return environment.get_template(f"{card}.html").render(**fields)


def _t2i(endpoint: str, html: str) -> bytes:
    body = json.dumps({"html": html, "json": False, "options": {}}, ensure_ascii=False).encode()
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/text2img/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        data = response.read()
    if not data:
        raise RuntimeError("t2i 返回了空图片")
    return data


def _emit(args: argparse.Namespace, card: str, fields: dict[str, Any]) -> int:
    html = _html(args.root, card, fields)
    if args.html:
        Path(args.html).write_text(html, encoding="utf-8")
        print(str(args.html))
        return 0
    out = Path(args.out) if args.out else Path(tempfile.mkstemp(prefix="alicedev-card-", suffix=".png")[1])
    try:
        out.write_bytes(_t2i(args.t2i, html))
    except (OSError, urllib.error.URLError, RuntimeError) as exc:
        print(f"渲染失败：{exc}", file=sys.stderr)
        return 1
    print(str(out))
    return 0


def _cmd_card(args: argparse.Namespace) -> int:
    reg = load_registry(args.root)
    try:
        card, fields = _card_for(reg, args.name)
    except KeyError:
        print(f"没有这条指令或场景：{args.name}", file=sys.stderr)
        return 1
    return _emit(args, card, fields)


def _inline_images(value: Any) -> Any:
    from alicedev.images import image_data_uri

    if isinstance(value, dict):
        return {k: _inline_images(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_inline_images(v) for v in value]
    if isinstance(value, str) and value.lower().endswith(_IMAGE_SUFFIXES) and Path(value).is_file():
        return image_data_uri(Path(value))
    return value


def _cmd_render(args: argparse.Namespace) -> int:
    raw = sys.stdin.read() if args.fields == "-" else Path(args.fields).read_text(encoding="utf-8")
    fields = json.loads(raw)
    if not isinstance(fields, dict):
        print("--fields 必须是 JSON 对象", file=sys.stderr)
        return 1
    if not (args.root / "cards" / f"{args.card}.html").is_file():
        print(f"卡片模板不存在：cards/{args.card}.html", file=sys.stderr)
        return 1
    return _emit(args, args.card, _inline_images(fields))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m alicedev.dsl")
    parser.add_argument("--root", type=Path, default=_DEFAULT_ROOT, help="templates/ 目录")
    commands = parser.add_subparsers(dest="action", required=True)

    check = commands.add_parser("check", help="校验 DSL")
    check.add_argument("path", nargs="?", type=Path, help="templates/ 目录（覆盖 --root）")
    check.set_defaults(handler=_cmd_check)

    for name, handler in (("card", _cmd_card), ("render", _cmd_render)):
        sub = commands.add_parser(name)
        if name == "card":
            sub.add_argument("name", help="指令名/别名或场景名")
        else:
            sub.add_argument("card", help="templates/cards 下的卡片名")
            sub.add_argument("--fields", required=True, help="字段 JSON 文件，或 - 从标准输入读取")
        sub.add_argument("--out", help="输出 PNG 路径（缺省写临时文件）")
        sub.add_argument("--html", help="只输出 HTML 到该路径，不调用 t2i")
        sub.add_argument("--t2i", default=os.environ.get("T2I_URL", "http://t2i:8999"))
        sub.set_defaults(handler=handler)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if getattr(args, "path", None):
        args.root = args.path
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
