"""DSL validation: bad files are rejected with file:line, the rest still load."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import jinja2
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))

from alicedev.dsl import context, load_registry, render, reply_instructions  # noqa: E402
from alicedev.dsl.messages import required_keys  # noqa: E402
from alicedev.dsl.model import AgentState, Tmpl  # noqa: E402
from alicedev.dsl.render import PROMPT_VARS  # noqa: E402


@pytest.fixture()
def tpl(tmp_path: Path) -> Path:
    dest = tmp_path / "templates"
    shutil.copytree(ROOT / "templates", dest)
    return dest


def write(root: Path, rel: str, text: str) -> None:
    (root / rel).write_text(text, encoding="utf-8")


def errors_for(root: Path, rel: str) -> list:
    reg = load_registry(root)
    return [e for e in reg.errors if e.path == rel]


GOOD_PING = """\
name: ping
type: program
summary: 测试
usage: /ping [%会话]
examples:
  - /ping %2
permission: all
args:
  会话: session
do:
  session_show:
    session: 会话
"""


def test_good_extra_command_loads(tpl: Path) -> None:
    write(tpl, "commands/ping.yaml", GOOD_PING)
    reg = load_registry(tpl)
    assert reg.errors == ()
    assert "ping" in reg.commands


@pytest.mark.parametrize(
    ("patch", "needle"),
    [
        # unquoted-# truncation: YAML drops " #会话", the usage no longer declares the arg
        (("usage: /ping [%会话]", "usage: /ping #会话"), "没有出现在 usage"),
        # an example that does not parse against usage
        (("  - /ping %2", "  - /ping [x]"), "示例"),
        # a value starting with % is a YAML error, reported loudly (never silently truncated)
        (("  - /ping %2", "  - %2"), "YAML"),
        (("summary: 测试", "summary: 测试\nfoo: 1"), "未知的键：foo"),
        (("  session_show:", "  teleport:"), "未知的动作"),
        (("type: program", "type: ai"), "属于程序指令"),
        (("  会话: session\ndo", "  会话: sesion\ndo"), "未知的参数类型"),
        (("permission: all", "permission: everyone"), "未知的权限"),
    ],
)
def test_bad_command_rejected_others_survive(tpl: Path, patch: tuple[str, str], needle: str) -> None:
    old, new = patch
    assert old in GOOD_PING
    write(tpl, "commands/ping.yaml", GOOD_PING.replace(old, new))
    reg = load_registry(tpl)
    errs = [e for e in reg.errors if e.path == "commands/ping.yaml"]
    assert len(errs) == 1 and needle in errs[0].message, errs
    assert errs[0].line >= 1
    assert "ping" not in reg.commands
    assert len(reg.command_list()) == 15  # the real commands still load


def test_hash_truncated_example_is_caught(tpl: Path) -> None:
    path = tpl / "commands/继续.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("  - /继续 %3 这个方案的风险呢", "  - /继续 #3 这个方案的风险呢"),
        encoding="utf-8",
    )
    (err,) = errors_for(tpl, "commands/继续.yaml")
    assert "示例按 usage 解析失败" in err.message


def test_error_line_points_at_key(tpl: Path) -> None:
    write(tpl, "commands/ping.yaml", GOOD_PING.replace("summary: 测试", "summary: 测试\nfoo: 1"))
    (err,) = errors_for(tpl, "commands/ping.yaml")
    assert err.line == 4


def test_say_key_must_be_action_result(tpl: Path) -> None:
    write(tpl, "commands/ping.yaml", GOOD_PING + "say:\n  ok: hi\n")
    (err,) = errors_for(tpl, "commands/ping.yaml")
    assert "不是动作 session_show 的结果" in err.message


def test_undefined_template_variable_rejected(tpl: Path) -> None:
    text = GOOD_PING + 'say:\n  not_found: "{{ nope }}"\n'
    write(tpl, "commands/ping.yaml", text)
    (err,) = errors_for(tpl, "commands/ping.yaml")
    assert "nope" in err.message


def test_alias_conflict_rejected(tpl: Path) -> None:
    write(tpl, "commands/ping.yaml", GOOD_PING.replace("summary: 测试", "summary: 测试\naliases: [help]"))
    (err,) = errors_for(tpl, "commands/ping.yaml")
    assert "冲突" in err.message


def test_filename_must_match_name(tpl: Path) -> None:
    write(tpl, "commands/pong.yaml", GOOD_PING)
    (err,) = errors_for(tpl, "commands/pong.yaml")
    assert "文件名" in err.message


def test_rejected_scenario_takes_dependent_commands_down(tpl: Path) -> None:
    path = tpl / "scenarios/requirement/scenario.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("initial: discussing", "initial: nowhere"), encoding="utf-8")
    reg = load_registry(tpl)
    paths = {e.path for e in reg.errors}
    assert "scenarios/requirement/scenario.yaml" in paths
    assert "commands/需求.yaml" in paths  # start: requirement now dangles
    assert "commands/需求列表.yaml" in paths
    assert "帮我调查" in reg.commands


def test_prompt_with_unknown_variable_rejected(tpl: Path) -> None:
    prompt = tpl / "scenarios/requirement/discussing.md"
    prompt.write_text(prompt.read_text(encoding="utf-8") + "\n{{ session_ref }}\n", encoding="utf-8")
    reg = load_registry(tpl)
    (err,) = [e for e in reg.errors if e.path == "scenarios/requirement/scenario.yaml"]
    assert "session_ref" in err.message


def test_ai_enterable_state_needs_reply_spec(tpl: Path) -> None:
    path = tpl / "scenarios/upgrade-bot/scenario.yaml"
    text = path.read_text(encoding="utf-8").replace(
        "  active:\n    kind: terminal\n    data: [commit]\n    reply:\n      kinds: [text]\n",
        "  active:\n    kind: terminal\n    data: [commit]\n",
    )
    path.write_text(text, encoding="utf-8")
    reg = load_registry(tpl)
    (err,) = [e for e in reg.errors if e.path == "scenarios/upgrade-bot/scenario.yaml"]
    assert "reply" in err.message


def test_messages_must_cover_every_result(tpl: Path) -> None:
    path = tpl / "messages.yaml"
    path.write_text(
        "\n".join(l for l in path.read_text(encoding="utf-8").splitlines() if not l.startswith("send.busy")),
        encoding="utf-8",
    )
    reg = load_registry(tpl)
    (err,) = [e for e in reg.errors if e.path == "messages.yaml"]
    assert "send.busy" in err.message


def test_real_messages_cover_required_keys() -> None:
    reg = load_registry(ROOT / "templates")
    assert set(required_keys()) <= set(reg.messages)


def test_render_is_strict() -> None:
    with pytest.raises(jinja2.UndefinedError):
        render(Tmpl("{{ missing }}"), {})
    assert render(Tmpl("%{{ 会话.no }}"), {"会话": {"no": 3}}) == "%3"


def test_every_prompt_renders_with_full_context() -> None:
    reg = load_registry(ROOT / "templates")
    github = {
        "kind": "issue", "owner": "o", "repo": "r", "number": 1, "title": "t",
        "body": "b", "labels": ["x"], "state": "open", "url": "https://x",
    }
    repo = {"fixed_main": "/workspace/alicedev", "worktree": "/w", "branch": "b", "base_sha": "abc"}
    for scenario in reg.scenarios.values():
        for state in scenario.states.values():
            if not isinstance(state, AgentState):
                continue
            for data in ({}, {"issue": "i", "pr": "p", "commit": "c"}):
                ctx = context(
                    PROMPT_VARS,
                    {
                        "text": "需求", "sender": {"id": "u", "name": "n"}, "chat": {"key": "k", "name": "g"},
                        "session": {"no": 3, "name": "s"}, "reports_dir": "/r/s1/", "data": data,
                        "state": state.name, "next": list(state.next), "agent_ref": "a_x",
                        "github": github if scenario.name.startswith("github") else None,
                        "repo": repo if scenario.name == "upgrade-bot" else None,
                    },
                )
                out = render(state.prompt, ctx)
                assert "%3" in out
            appendix = reply_instructions(scenario, state)
            assert "chat_reply" in appendix


def test_reply_instructions_for_upgrade_working() -> None:
    reg = load_registry(ROOT / "templates")
    scenario = reg.scenarios["upgrade-bot"]
    text = reply_instructions(scenario, scenario.states["working"])  # type: ignore[arg-type]
    assert "awaiting_approval" in text and "issue, pr, commit" in text
    assert "必须同时附带 reply" in text and "image" in text
    assert "不会发到群里" in text
