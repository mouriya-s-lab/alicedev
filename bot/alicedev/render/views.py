"""Pure view-model builders for DSL-driven cards (ARCHITECTURE §3.5).

Each builder returns ``(card_name, fields)``; the runtime hands them to
``CardRenderer.render(card_name, fields)``. Nothing here performs I/O.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from alicedev.domain import FavoriteView, SessionView
from alicedev.dsl.model import (
    AgentState,
    ArgType,
    Command,
    CommandType,
    GithubAction,
    HumanState,
    Permission,
    Registry,
    RepoWorkdir,
    Scenario,
    StartAction,
    TerminalState,
    is_visible,
    ARCHIVED,
    FAILED,
    MAIN_SYNC_FAILED,
    QUEUED,
)

Card = tuple[str, dict[str, Any]]

_PERMISSION_LABELS = {
    Permission.ALL: "所有人",
    Permission.OWNER: "会话创建者或管理员",
    Permission.ADMIN: "管理员",
}
_TYPE_LABELS = {CommandType.AI: "AI 指令", CommandType.PROGRAM: "程序指令"}
_ARG_LABELS = {
    ArgType.SESSION: "会话号（%n）",
    ArgType.TEXT: "文本",
    ArgType.WORD: "一个词",
    ArgType.INT: "整数",
    ArgType.PAGE: "页码",
    ArgType.GITHUB_REF: "GitHub 链接或 #编号",
    ArgType.MENTIONS: "@ 用户",
    ArgType.QUOTED: "引用一条消息",
}
_BUILTIN_LABELS = {
    QUEUED: "排队中",
    MAIN_SYNC_FAILED: "main 对齐失败",
    FAILED: "创建失败",
    ARCHIVED: "已归档",
}


def _time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value is not None else ""


def _footer(command: str, page: int, pages: int) -> str:
    return f"第 {page}/{pages} 页 · /{command} n"


# --- scenario flow -------------------------------------------------------------


def scenario_flow(scenario: Scenario, current_state: str | None = None) -> dict[str, Any]:
    """States and transitions as plain data (no mermaid; the card lays it out)."""

    states: list[dict[str, Any]] = [
        {
            "name": QUEUED,
            "kind": "内建",
            "visible": False,
            "current": current_state == QUEUED,
            "edges": [{"to": scenario.initial, "label": "派发"}],
        }
    ]
    for state in scenario.states.values():
        match state:
            case AgentState():
                kind = "AI 对话" if state.conversational else "AI 工作（内部）"
                edges = [{"to": t, "label": "AI 报告"} for t in state.next]
            case HumanState():
                kind = "等待人工"
                edges = [{"to": t, "label": f"/{c}"} for c, t in state.commands.items()]
            case TerminalState():
                kind = "结束"
                edges = []
        states.append(
            {
                "name": state.name,
                "kind": kind,
                "visible": is_visible(state),
                "current": current_state == state.name,
                "share": state.share,
                "data": list(state.data),
                "edges": edges,
            }
        )
    terminals = [
        {"name": name, "label": _BUILTIN_LABELS[name], "current": current_state == name}
        for name in (MAIN_SYNC_FAILED, FAILED, ARCHIVED)
        if name != MAIN_SYNC_FAILED or isinstance(scenario.workdir, RepoWorkdir)
    ]
    return {
        "name": scenario.name,
        "description": scenario.description,
        "states": states,
        "builtin_terminals": terminals,
    }


def state_label(scenario: Scenario | None, state: str) -> str:
    if state in _BUILTIN_LABELS:
        return _BUILTIN_LABELS[state]
    if scenario is None or state not in scenario.states:
        return state
    match scenario.states[state]:
        case AgentState() as s:
            return "对话中" if s.conversational else f"AI 工作中（{state}）"
        case HumanState():
            return f"等待处理（{state}）"
        case TerminalState():
            return f"已结束（{state}）"
    return state


# --- help & command cards ------------------------------------------------------------


def help_card(reg: Registry, current: SessionView | None, scenario: Scenario | None = None) -> Card:
    groups: list[dict[str, Any]] = []
    for command_type in (CommandType.AI, CommandType.PROGRAM):
        entries = []
        for command in reg.command_list():
            for cmd in (command, *command.subcommands.values()):
                if cmd.type is not command_type:
                    continue
                entries.append(
                    {
                        "usage": cmd.usage,
                        "summary": cmd.summary,
                        "admin": cmd.permission is not Permission.ALL,
                        "permission": _PERMISSION_LABELS[cmd.permission],
                    }
                )
        groups.append({"title": _TYPE_LABELS[command_type], "entries": entries})
    fields: dict[str, Any] = {
        "title": "使用帮助",
        "subtitle": "发送 /alicedev <指令名> 查看某条指令的详细说明",
        "groups": groups,
        "current": _session_brief(current, scenario),
        "rules": [
            "AI 指令会新开一个会话，或把内容发到已有会话；会话用 %n 指代（全角 ％ 也可以）。",
            "不写 %n 时，先取你引用的那条 bot 消息所属的会话，没有引用就用本群当前会话。",
            "直接 @bot 或私聊发的自然消息，会发到当前会话。",
        ],
        "footer": f"共 {len(reg.command_list())} 条指令",
    }
    return "help", fields


def command_card(command: Command, scenario: Scenario | None) -> Card:
    def entry(cmd: Command) -> dict[str, Any]:
        return {
            "usage": cmd.usage,
            "summary": cmd.summary,
            "type": _TYPE_LABELS[cmd.type],
            "permission": _PERMISSION_LABELS[cmd.permission],
            "params": [
                {"name": p.name, "type": _ARG_LABELS[p.type], "optional": p.optional}
                for p in cmd.params
            ],
            "examples": list(cmd.examples),
        }

    fields: dict[str, Any] = {
        "title": f"/{command.name}",
        "subtitle": command.summary,
        "aliases": list(command.aliases),
        "main": entry(command),
        "subcommands": [entry(sub) for sub in command.subcommands.values()],
        "flow": scenario_flow(scenario) if scenario is not None else None,
    }
    return "command", fields


def command_scenario(reg: Registry, command: Command) -> Scenario | None:
    """The scenario a new-session command starts (first one for github)."""

    match command.do:
        case StartAction():
            return reg.scenarios.get(command.do.scenario)
        case GithubAction():
            return reg.scenarios.get(command.do.scenarios.get("issue", ""))
    return None


# --- session cards -----------------------------------------------------------------


def _session_brief(view: SessionView | None, scenario: Scenario | None) -> dict[str, Any] | None:
    if view is None:
        return None
    return {
        "label": view.label,
        "name": view.name,
        "state": state_label(scenario, view.state),
    }


def session_card(view: SessionView, scenario: Scenario | None) -> Card:
    fields = {
        "title": f"{view.label} {view.name}",
        "subtitle": "当前会话" if view.is_current else "",
        "scenario": scenario.name if scenario is not None else view.scenario,
        "scenario_description": scenario.description if scenario is not None else "",
        "state": state_label(scenario, view.state),
        "created_by": view.created_by,
        "created_at": _time(view.created_at),
        "last_activity_at": _time(view.last_activity_at),
        "is_current": view.is_current,
        "flow": scenario_flow(scenario, view.state) if scenario is not None else None,
    }
    return "session", fields


def session_detail(view: SessionView, scenario: Scenario | None) -> dict[str, Any]:
    """``GET /v1/sessions/{id}`` body for the gateway's session page (ARCHITECTURE §6/§8)."""
    return {
        "label": view.label,
        "name": view.name,
        "scenario": {
            "name": scenario.name if scenario is not None else view.scenario,
            "description": scenario.description if scenario is not None else "",
        },
        "state": {"name": view.state, "label": state_label(scenario, view.state)},
        "created_by": view.created_by,
        "created_at": _time(view.created_at),
        "last_activity_at": _time(view.last_activity_at) or None,
        "is_current": view.is_current,
    }


def session_list_card(
    rows: Sequence[SessionView],
    page: int,
    pages: int,
    *,
    archived: bool,
    command: str,
    scenarios: Mapping[str, Scenario] | None = None,
) -> Card:
    scenarios = scenarios or {}
    fields = {
        "title": "全部会话" if archived else "会话列表",
        "subtitle": "含已结束的会话" if archived else "进行中的会话",
        "rows": [
            {
                "label": row.label,
                "name": row.name,
                "scenario": row.scenario,
                "state": state_label(scenarios.get(row.scenario), row.state),
                "last_activity_at": _time(row.last_activity_at or row.created_at),
                "current": row.is_current,
            }
            for row in rows
        ],
        "footer": _footer(command, page, pages),
    }
    return "session_list", fields


def requirements_list_card(
    rows: Sequence[SessionView],
    page: int,
    pages: int,
    *,
    archived: bool,
    command: str,
    scenarios: Mapping[str, Scenario] | None = None,
) -> Card:
    del archived
    scenarios = scenarios or {}
    fields = {
        "title": "需求列表",
        "subtitle": "每条需求都是一个会话，可用 %n 继续",
        "rows": [
            {
                "label": row.label,
                "text": row.name,
                "author_name": row.created_by,
                "created_at": _time(row.created_at),
                "status": state_label(scenarios.get(row.scenario), row.state),
            }
            for row in rows
        ],
        "footer": _footer(command, page, pages),
    }
    return "requirements_list", fields


def favorites_list_card(
    rows: Sequence[FavoriteView], page: int, pages: int, *, command: str
) -> Card:
    fields = {
        "title": "收藏夹",
        "rows": [
            {
                "id": row.id,
                "text": row.text,
                "author_name": row.author_name,
                "saver_name": row.saver_name,
                "created_at": _time(row.created_at),
                "images": list(row.images),
            }
            for row in rows
        ],
        "footer": _footer(command, page, pages),
    }
    return "favorites_list", fields


# ``list`` action card name -> builder for session rows (favorites are separate).
SESSION_LIST_CARDS: Mapping[str, Callable[..., Card]] = {
    "session_list": session_list_card,
    "requirements_list": requirements_list_card,
}
