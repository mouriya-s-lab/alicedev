"""Agent-callable command and admin scenario DSL boundaries."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))

from alicedev.dsl import load_registry, reply_instructions  # noqa: E402
from alicedev.dsl.model import AgentState, Audience  # noqa: E402

@pytest.fixture()
def tpl(tmp_path: Path) -> Path:
    dest = tmp_path / "templates"
    shutil.copytree(ROOT / "templates", dest)
    return dest


def replace_once(root: Path, relative: str, before: str, after: str) -> None:
    path = root / relative
    original = path.read_text(encoding="utf-8")
    assert original.count(before) == 1, f"fixture no longer has exactly one {before!r} in {relative}"
    path.write_text(original.replace(before, after), encoding="utf-8")


def rejection(registry, relative: str, fragment: str) -> None:
    assert any(e.path == relative and fragment in e.message for e in registry.errors), registry.errors


def test_unmodified_agent_rule_fixtures_resolve(tpl: Path) -> None:
    registry = load_registry(tpl)
    assert not registry.errors
    assert registry.scenarios["steward"].audience is Audience.ADMIN
    assert registry.scenarios["steward"].one_per_chat
    assert registry.command_at("管家") is not None
    assert registry.command_at("群列表") is not None
    assert registry.command_at("会话列表 全部") is not None
    assert registry.command_at("帮我调查") is not None
    assert registry.command_at("sl 全部") is None
    assert registry.command_at("req") is None  # aliases are not canonical agent-command paths


@pytest.mark.parametrize("path", ["根本不存在", "会话列表 不存在"])
def test_unknown_agent_command_rejects_scenario_and_starter(tpl: Path, path: str) -> None:
    replace_once(tpl, "scenarios/requirement/scenario.yaml", "agent_commands: [需求,", f"agent_commands: [{path},")
    registry = load_registry(tpl)
    rejection(registry, "scenarios/requirement/scenario.yaml", path)
    rejection(registry, "commands/需求.yaml", "requirement")
    assert "requirement" not in registry.scenarios
    assert registry.command_at("需求") is None
    assert registry.command_at("req") is None
    assert registry.command_at("帮我调查") is not None


@pytest.mark.parametrize("path, action", [
    ("继续", "send"),
    ("链接", "share"),
    ("升级bot approve", "human"),
    ("alicedev", "help"),
])
def test_non_callable_action_rejects_agent_scenario(tpl: Path, path: str, action: str) -> None:
    replace_once(tpl, "scenarios/requirement/scenario.yaml", "agent_commands: [需求,", f"agent_commands: [{path},")
    registry = load_registry(tpl)
    rejection(registry, "scenarios/requirement/scenario.yaml", action)
    assert "requirement" not in registry.scenarios
    assert registry.command_at("需求") is None
    assert registry.command_at(path) is not None  # its own command remains valid for humans
    assert registry.command_at("帮我调查") is not None


def test_admin_agent_command_requires_admin_audience(tpl: Path) -> None:
    replace_once(tpl, "scenarios/requirement/scenario.yaml", "agent_commands: [需求,", "agent_commands: [群列表,")
    registry = load_registry(tpl)
    rejection(registry, "scenarios/requirement/scenario.yaml", "群列表")
    assert "requirement" not in registry.scenarios
    assert registry.command_at("需求") is None
    steward = registry.scenarios["steward"]
    state = steward.states["discussing"]
    assert isinstance(state, AgentState)
    assert "群列表" in state.agent_commands
    assert registry.command_at("群列表") is not None
    assert registry.command_at("管家") is not None


def test_canonical_subcommand_path_is_agent_callable(tpl: Path) -> None:
    replace_once(tpl, "scenarios/requirement/scenario.yaml", "agent_commands: [需求,", "agent_commands: [会话列表 全部,")
    registry = load_registry(tpl)
    assert not registry.errors
    assert "requirement" in registry.scenarios
    command = registry.command_at("会话列表 全部")
    assert command is not None and command.usage.startswith("/会话列表 全部")
    assert "会话列表 全部" in registry.scenarios["requirement"].states["discussing"].agent_commands
    assert registry.command_at("需求") is not None


@pytest.mark.parametrize("path", ["sl", "sl 全部", "req"])
def test_alias_is_not_an_agent_command_path(tpl: Path, path: str) -> None:
    replace_once(tpl, "scenarios/requirement/scenario.yaml", "agent_commands: [需求,", f"agent_commands: [{path},")
    registry = load_registry(tpl)
    rejection(registry, "scenarios/requirement/scenario.yaml", path)
    assert registry.command_at(path) is None
    assert registry.command_at("会话列表 全部") is not None
    assert "requirement" not in registry.scenarios
    assert registry.command_at("需求") is None
    assert registry.command_at("帮我调查") is not None


def test_non_admin_command_cannot_start_admin_scenario(tpl: Path) -> None:
    replace_once(tpl, "commands/管家.yaml", "permission: admin", "permission: all")
    registry = load_registry(tpl)
    rejection(registry, "commands/管家.yaml", "permission: admin")
    assert "steward" in registry.scenarios
    assert registry.command_at("管家") is None
    assert registry.command_at("steward") is None
    assert registry.command_at("帮我调查") is not None


def test_route_cannot_start_admin_scenario(tpl: Path) -> None:
    (tpl / "routes.yaml").write_text(
        '- when: addressed\n  do: {start: {scenario: steward, text: "{{ message }}"}}\n',
        encoding="utf-8",
    )
    registry = load_registry(tpl)
    rejection(registry, "routes.yaml", "steward")
    assert registry.routes == ()
    assert "steward" in registry.scenarios
    assert registry.command_at("管家") is not None
    assert registry.command_at("帮我调查") is not None


def test_one_per_chat_cannot_be_exclusive(tpl: Path) -> None:
    replace_once(tpl, "scenarios/steward/scenario.yaml", "one_per_chat: true", "one_per_chat: true\nexclusive: true")
    registry = load_registry(tpl)
    rejection(registry, "scenarios/steward/scenario.yaml", "one_per_chat")
    assert "steward" not in registry.scenarios
    assert registry.command_at("管家") is None
    assert registry.command_at("帮我调查") is not None


def test_broken_command_does_not_reject_scenarios_that_list_it(tpl: Path) -> None:
    replace_once(tpl, "commands/需求.yaml", "permission: all", "permission: unknown")
    registry = load_registry(tpl)
    rejection(registry, "commands/需求.yaml", "unknown")
    assert {"requirement", "investigate", "github-issue", "github-pr", "steward"} <= registry.scenarios.keys()
    assert registry.command_at("需求") is None
    assert registry.command_at("req") is None
    assert registry.command_at("帮我调查") is not None
    assert registry.command_at("解读") is not None
    assert registry.command_at("管家") is not None
    assert not any(e.path.startswith("scenarios/") for e in registry.errors)


def test_reply_instructions_include_steward_commands_only_where_declared(tpl: Path) -> None:
    registry = load_registry(tpl)
    assert not registry.errors
    steward = registry.scenarios["steward"]
    discussing = steward.states["discussing"]
    assert isinstance(discussing, AgentState)
    commands = [(path, registry.command_at(path)) for path in discussing.agent_commands]
    assert all(command is not None for _, command in commands)
    agent_ref = "a_test23456"
    text = reply_instructions(steward, discussing, agent_ref=agent_ref, commands=commands)
    assert "### 可用指令" in text
    assert f"alicedev run --agent {agent_ref}" in text
    assert "alicedev commands --agent" in text
    for path, command in commands:
        assert f"`{command.usage}`（{path}）" in text

    upgrade = registry.scenarios["upgrade-bot"]
    working = upgrade.states["working"]
    assert isinstance(working, AgentState)
    assert working.agent_commands == ()
    upgrade_text = reply_instructions(upgrade, working, agent_ref=agent_ref, commands=[])
    assert "chat_reply" in upgrade_text
    assert "可用指令" not in upgrade_text
