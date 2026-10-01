"""Independent tool capabilities and genuine chat/scenario DSL boundaries."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))

from alicedev.agent_tools.model import ADMIN_TOOLS, ToolName  # noqa: E402
from alicedev.dsl import load_registry  # noqa: E402
from alicedev.dsl.messages import required_keys  # noqa: E402
from alicedev.dsl.model import (  # noqa: E402
    AgentState,
    ListAction,
    ListSource,
    Registry,
    RequirementAddAction,
)
from alicedev.dsl.render import render  # noqa: E402


YamlValue = str | int | bool | None | list["YamlValue"] | dict[str, "YamlValue"]


def write_yaml(root: Path, relative: str, value: YamlValue) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")


def scenario(root: Path, name: str, *, tools: YamlValue, audience: str = "all", **extra: bool) -> None:
    write_yaml(root, f"scenarios/{name}/scenario.yaml", {
        "name": name,
        "title": "调查 · {{ text }}",
        "provider": "omp-alicedev/opencode-go/muse-spark-1.3-contributor",
        "cwd": "/workspace/openalice",
        "audience": audience,
        "initial": "discussing",
        "states": {"discussing": {
            "kind": "agent", "prompt": "discussing.md", "reply": {"kinds": ["text"]}, "tools": tools,
        }},
        **extra,
    })
    (root / f"scenarios/{name}/discussing.md").write_text("调查 {{ text }}。", encoding="utf-8")


def command(root: Path, name: str, action: YamlValue, *, kind: str = "program", permission: str = "all") -> None:
    write_yaml(root, f"commands/{name}.yaml", {
        "name": name, "type": kind, "summary": "边界测试", "usage": f"/{name} <内容>",
        "permission": permission, "args": {"内容": "text"}, "do": action,
    })


@pytest.fixture()
def tpl(tmp_path: Path) -> Path:
    root = tmp_path / "templates"
    write_yaml(root, "messages.yaml", {key: "结果" for key in required_keys()})
    for card in ("session_list", "requirements_list", "favorites_list"):
        path = root / f"cards/{card}.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<p>结果</p>", encoding="utf-8")
    scenario(root, "investigate", tools=["session_get", "requirement_add", "favorites_list"])
    scenario(root, "steward", tools=[tool.value for tool in ToolName], audience="admin", one_per_chat=True)
    command(root, "调查", {"start": {"scenario": "investigate", "text": "{{ 内容 }}"}}, kind="ai")
    command(root, "管家", {"start": {"scenario": "steward", "text": "{{ 内容 }}"}}, kind="ai", permission="admin")
    return root


def rejection(registry: Registry, relative: str, fragment: str) -> None:
    assert any(error.path == relative and fragment in error.message for error in registry.errors), registry.errors


def test_tools_load_as_ordered_enum_without_chat_command_definitions(tpl: Path) -> None:
    registry = load_registry(tpl)
    assert not registry.errors
    state = registry.scenarios["investigate"].states["discussing"]
    assert isinstance(state, AgentState)
    assert state.tools == (ToolName.SESSION_GET, ToolName.REQUIREMENT_ADD, ToolName.FAVORITES_LIST)
    assert all(isinstance(tool, ToolName) for tool in state.tools)


@pytest.mark.parametrize("name", ["unknown_tool", "会话", "session_get extra", " session_get ", "SESSION_GET"])
def test_unknown_tool_rejects_scenario_and_its_chat_starter(tpl: Path, name: str) -> None:
    scenario(tpl, "investigate", tools=[name])
    registry = load_registry(tpl)
    rejection(registry, "scenarios/investigate/scenario.yaml", name)
    assert "investigate" not in registry.scenarios
    assert registry.command_at("调查") is None
    assert registry.command_at("管家") is not None


@pytest.mark.parametrize("tools", ["session_get", {"session_get": {}}, ["session_get", 1]])
def test_tool_declaration_requires_a_string_list(tpl: Path, tools: YamlValue) -> None:
    scenario(tpl, "investigate", tools=tools)
    registry = load_registry(tpl)
    rejection(registry, "scenarios/investigate/scenario.yaml", "tools")
    assert "investigate" not in registry.scenarios


def test_duplicate_tools_reject_scenario(tpl: Path) -> None:
    scenario(tpl, "investigate", tools=["session_get", "session_get"])
    registry = load_registry(tpl)
    rejection(registry, "scenarios/investigate/scenario.yaml", "session_get")
    assert "investigate" not in registry.scenarios


@pytest.mark.parametrize("tool", sorted(ADMIN_TOOLS, key=lambda tool: tool.value))
def test_admin_tool_requires_admin_audience(tpl: Path, tool: ToolName) -> None:
    scenario(tpl, "investigate", tools=[tool.value])
    registry = load_registry(tpl)
    rejection(registry, "scenarios/investigate/scenario.yaml", tool.value)
    assert "investigate" not in registry.scenarios
    admin_state = registry.scenarios["steward"].states["discussing"]
    assert isinstance(admin_state, AgentState)
    assert tool in admin_state.tools
    assert registry.command_at("管家") is not None


def test_old_agent_commands_key_is_unknown_even_when_tools_present(tpl: Path) -> None:
    path = tpl / "scenarios/investigate/scenario.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["states"]["discussing"]["agent_commands"] = ["会话"]
    write_yaml(tpl, "scenarios/investigate/scenario.yaml", data)
    registry = load_registry(tpl)
    rejection(registry, "scenarios/investigate/scenario.yaml", "agent_commands")
    assert "investigate" not in registry.scenarios


def test_broken_chat_command_does_not_reject_independent_tools(tpl: Path) -> None:
    command(tpl, "session_get", {"session_show": {}}, permission="unknown")
    registry = load_registry(tpl)
    rejection(registry, "commands/session_get.yaml", "unknown")
    assert {"investigate", "steward"} <= registry.scenarios.keys()
    assert registry.command_at("调查") is not None
    assert not any(error.path.startswith("scenarios/") for error in registry.errors)


def test_chat_command_with_non_agent_action_does_not_limit_tool(tpl: Path) -> None:
    command(tpl, "session_get", {"send": {"text": "{{ 内容 }}"}}, kind="ai")
    registry = load_registry(tpl)
    assert not registry.errors
    state = registry.scenarios["investigate"].states["discussing"]
    assert isinstance(state, AgentState)
    assert ToolName.SESSION_GET in state.tools


def test_non_admin_chat_command_cannot_start_admin_scenario(tpl: Path) -> None:
    command(tpl, "管家", {"start": {"scenario": "steward", "text": "{{ 内容 }}"}}, kind="ai")
    registry = load_registry(tpl)
    rejection(registry, "commands/管家.yaml", "permission: admin")
    assert "steward" in registry.scenarios
    assert registry.command_at("管家") is None
    assert registry.command_at("调查") is not None


def test_route_cannot_start_admin_scenario(tpl: Path) -> None:
    write_yaml(tpl, "routes.yaml", [{"when": "addressed", "do": {"start": {
        "scenario": "steward", "text": "{{ message }}",
    }}}])
    registry = load_registry(tpl)
    rejection(registry, "routes.yaml", "steward")
    assert registry.routes == ()
    assert "steward" in registry.scenarios
    assert registry.command_at("管家") is not None


def test_one_per_chat_cannot_be_exclusive(tpl: Path) -> None:
    scenario(tpl, "steward", tools=["session_get"], audience="admin", one_per_chat=True, exclusive=True)
    registry = load_registry(tpl)
    rejection(registry, "scenarios/steward/scenario.yaml", "one_per_chat")
    assert "steward" not in registry.scenarios
    assert registry.command_at("管家") is None
    assert registry.command_at("调查") is not None


def test_requirement_add_parses_and_renders_command_text(tpl: Path) -> None:
    command(tpl, "需求", {"requirement_add": {"text": "{{ sender.name }}: {{ 内容 }}"}})
    registry = load_registry(tpl)
    assert not registry.errors
    parsed = registry.command_at("需求")
    assert parsed is not None and isinstance(parsed.do, RequirementAddAction)
    assert render(parsed.do.text, {"sender": {"name": "Alice"}, "内容": "增加独立记录"}) == "Alice: 增加独立记录"


@pytest.mark.parametrize("body", [{}, {"text": "{{ 内容 }}", "scenario": "investigate"}, {"text": "{{ missing }}"}, {"text": True}])
def test_requirement_add_rejects_invalid_body(tpl: Path, body: YamlValue) -> None:
    command(tpl, "需求", {"requirement_add": body})
    registry = load_registry(tpl)
    assert registry.command_at("需求") is None
    assert any(error.path == "commands/需求.yaml" for error in registry.errors)


def test_requirement_add_is_not_an_ai_action(tpl: Path) -> None:
    command(tpl, "需求", {"requirement_add": {"text": "{{ 内容 }}"}}, kind="ai")
    registry = load_registry(tpl)
    rejection(registry, "commands/需求.yaml", "requirement_add")
    assert registry.command_at("需求") is None


def test_requirements_list_is_a_distinct_typed_source(tpl: Path) -> None:
    command(tpl, "需求列表", {"list": {"source": "requirements", "card": "requirements_list"}})
    registry = load_registry(tpl)
    assert not registry.errors
    parsed = registry.command_at("需求列表")
    assert parsed is not None and isinstance(parsed.do, ListAction)
    assert parsed.do.source is ListSource.REQUIREMENTS
    assert parsed.do.card == "requirements_list"


@pytest.mark.parametrize("source, card", [("sessions", "requirements_list"), ("requirements", "session_list"), ("favorites", "requirements_list")])
def test_list_source_cannot_use_another_sources_card(tpl: Path, source: str, card: str) -> None:
    command(tpl, "列表", {"list": {"source": source, "card": card}})
    registry = load_registry(tpl)
    rejection(registry, "commands/列表.yaml", source)
    assert registry.command_at("列表") is None


def test_requirement_list_cannot_filter_by_session_scenario(tpl: Path) -> None:
    command(tpl, "列表", {"list": {"source": "requirements", "card": "requirements_list", "scenario": "investigate"}})
    registry = load_registry(tpl)
    rejection(registry, "commands/列表.yaml", "sessions")
    assert registry.command_at("列表") is None
