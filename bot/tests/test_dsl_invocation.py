"""Command matching and argument parsing against the real templates/ DSL."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))

from alicedev.dsl import (  # noqa: E402
    ArgError,
    FromEvent,
    ParsedArgs,
    SessionArg,
    load_registry,
    match_command,
    parse_args,
)

REG = load_registry(ROOT / "templates")


def parse(text: str):
    inv = match_command(REG, text)
    assert inv is not None, text
    return inv, parse_args(inv.command, inv)


def test_real_templates_load_clean() -> None:
    assert REG.errors == ()
    assert len(REG.command_list()) == 15
    assert set(REG.scenarios) == {"requirement", "investigate", "github-issue", "github-pr", "upgrade-bot"}


@pytest.mark.parametrize(
    "text",
    ["/继续 %3 再看看", "/继续%3 再看看", "/继续 ％3 再看看", "/继续 ％３ 再看看", "／继续 %3 再看看", "/continue %3 再看看"],
)
def test_session_marker_variants(text: str) -> None:
    inv, args = parse(text)
    assert inv.command.name == "继续"
    assert isinstance(args, ParsedArgs)
    assert args.values == {"会话": SessionArg(no=3), "内容": "再看看"}


def test_session_omitted_defers_to_runtime() -> None:
    _, args = parse("/继续 补充一下")
    assert isinstance(args, ParsedArgs)
    assert args.values["会话"] == SessionArg(no=None)


def test_subcommand_then_marker() -> None:
    inv, args = parse("/升级bot approve %2")
    assert inv.path == ("升级bot", "approve")
    assert isinstance(args, ParsedArgs) and args.values == {"会话": SessionArg(no=2)}
    inv, args = parse("/升级bot 新增 /ping 指令")
    assert inv.path == ("升级bot",)
    assert isinstance(args, ParsedArgs) and args.values["需求"] == "新增 /ping 指令"


def test_list_subcommand_and_page() -> None:
    inv, args = parse("/会话列表 全部 2")
    assert inv.path == ("会话列表", "全部") and isinstance(args, ParsedArgs)
    assert args.values["页"] == 2
    _, args = parse("/会话列表")
    assert isinstance(args, ParsedArgs) and args.values["页"] == 1
    _, args = parse("/会话列表 x")
    assert isinstance(args, ArgError)


def test_required_session_and_text() -> None:
    _, args = parse("/切换")
    assert isinstance(args, ArgError)
    _, args = parse("/需求")
    assert isinstance(args, ArgError)


def test_percent_not_parsed_without_session_param() -> None:
    _, args = parse("/需求 %3 提升到 90%")
    assert isinstance(args, ParsedArgs) and args.values["内容"] == "%3 提升到 90%"


def test_github_ref_keeps_hash() -> None:
    _, args = parse("/解读 #12")
    assert isinstance(args, ParsedArgs) and args.values["链接"] == "#12"


def test_mentions_and_quoted_come_from_event() -> None:
    _, args = parse("/链接 %2 @小明 @小红")
    assert isinstance(args, ParsedArgs)
    assert args.values == {"会话": SessionArg(no=2), "用户": FromEvent.MENTIONS}
    _, args = parse("/收藏")
    assert isinstance(args, ParsedArgs) and args.values == {"引用消息": FromEvent.QUOTED}


def test_help_word_and_extra_text() -> None:
    _, args = parse("/alicedev 继续")
    assert isinstance(args, ParsedArgs) and args.values["指令名"] == "继续"
    _, args = parse("/alicedev 继续 多余")
    assert isinstance(args, ArgError)


def test_non_commands() -> None:
    assert match_command(REG, "你好") is None
    assert match_command(REG, "/不存在的指令 x") is None
    assert match_command(REG, "") is None
