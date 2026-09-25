"""Load and validate ``templates/`` into a :class:`Registry` (ARCHITECTURE §3.5).

Validation is per file: a file with any problem is rejected (reported as a
:class:`DslError` with path and line) and the rest still load. Commands that
depend on a rejected scenario are rejected as well. ``load_registry`` never
raises for content problems.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import yaml

from alicedev.dsl import render as tmpl
from alicedev.dsl.invocation import ArgError, match_command, parse_args
from alicedev.dsl.messages import ACTION_NAMES, required_keys
from alicedev.dsl.model import (
    AGENT_ACTIONS,
    AI_ACTIONS,
    BUILTIN_STATES,
    RESULT_KEYS,
    Action,
    AgentState,
    Audience,
    ArgType,
    ChatsAction,
    Command,
    CommandType,
    CwdWorkdir,
    DslError,
    FavoriteAction,
    GithubAction,
    HelpAction,
    HumanAction,
    HumanState,
    ListAction,
    ListSource,
    Permission,
    Registry,
    ReplySpec,
    RepoWorkdir,
    Route,
    RouteWhen,
    Scenario,
    SendAction,
    SessionArchiveAction,
    SessionRenameAction,
    SessionShowAction,
    SessionSwitchAction,
    ShareAction,
    SourceRef,
    StartAction,
    StatusAction,
    State,
    TerminalState,
    Tmpl,
)
from alicedev.dsl.usage import UsageProblem, parse_usage

REPLY_KINDS = frozenset({"text", "text_template", "image_template", "image", "sticker", "file"})
LIST_CARDS: Mapping[ListSource, frozenset[str]] = {
    ListSource.SESSIONS: frozenset({"session_list", "requirements_list"}),
    ListSource.FAVORITES: frozenset({"favorites_list"}),
}
# Actions that always render one fixed card (§3.2).
FIXED_CARDS: Mapping[type, str] = {ChatsAction: "chats_list", StatusAction: "bot_status"}


# --- YAML with line numbers ---------------------------------------------------


class LineMap(dict):
    """A dict that remembers the source line of the mapping and of each key."""

    line: int = 1

    def __init__(self) -> None:
        super().__init__()
        self.lines: dict[Any, int] = {}

    def at(self, key: str) -> int:
        return self.lines.get(key, self.line)


class _LineLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _LineLoader, node: yaml.MappingNode) -> LineMap:
    loader.flatten_mapping(node)
    mapping = LineMap()
    mapping.line = node.start_mark.line + 1
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in mapping:
            raise _Reject(key_node.start_mark.line + 1, f"键重复：{key}")
        mapping[key] = loader.construct_object(value_node, deep=True)
        mapping.lines[key] = key_node.start_mark.line + 1
    return mapping


_LineLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


class _Reject(Exception):
    def __init__(self, line: int, message: str) -> None:
        super().__init__(message)
        self.line = line
        self.message = message


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=_LineLoader)
    except _Reject:
        raise
    except yaml.MarkedYAMLError as exc:
        line = (exc.problem_mark.line + 1) if exc.problem_mark else 1
        raise _Reject(line, f"YAML 解析失败：{exc.problem}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise _Reject(1, f"无法读取文件：{exc}") from exc


# --- small field helpers --------------------------------------------------------


def _mapping(value: Any, line: int, what: str) -> LineMap:
    if not isinstance(value, LineMap):
        raise _Reject(line, f"{what} 必须是映射")
    return value


def _keys(m: LineMap, allowed: Iterable[str], required: Iterable[str], what: str) -> None:
    allowed_set = set(allowed)
    for key in m:
        if key not in allowed_set:
            raise _Reject(m.at(key), f"{what} 有未知的键：{key}")
    for key in required:
        if key not in m:
            raise _Reject(m.line, f"{what} 缺少必填键：{key}")


def _str(m: LineMap, key: str, *, required: bool = True, default: str = "") -> str:
    if key not in m:
        if required:
            raise _Reject(m.line, f"缺少 {key}")
        return default
    value = m[key]
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise _Reject(m.at(key), f"{key} 必须是字符串")
    return str(value)


def _strs(m: LineMap, key: str) -> tuple[str, ...]:
    if key not in m or m[key] is None:
        return ()
    value = m[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _Reject(m.at(key), f"{key} 必须是字符串列表")
    return tuple(value)


def _bool(m: LineMap, key: str) -> bool:
    if key not in m:
        return False
    value = m[key]
    if not isinstance(value, bool):
        raise _Reject(m.at(key), f"{key} 必须是 true/false")
    return value


def _enum(m: LineMap, key: str, enum: type, what: str) -> Any:
    raw = _str(m, key)
    try:
        return enum(raw)
    except ValueError as exc:
        choices = " | ".join(e.value for e in enum)
        raise _Reject(m.at(key), f"未知的{what}：{raw}（可选：{choices}）") from exc


def _tmpl(source: str, allowed: Iterable[str], line: int) -> Tmpl:
    try:
        tmpl.check(source, allowed)
    except tmpl.TemplateProblem as exc:
        raise _Reject(line, str(exc)) from exc
    return Tmpl(source)


# --- context shared across files -----------------------------------------------


@dataclass
class _Ctx:
    root: Path
    cards: frozenset[str]
    stickers: frozenset[str]
    scenarios: Mapping[str, Scenario]
    human_commands: frozenset[str]
    # (scenario, state) -> line of the state declaring agent_commands, for the cross check.
    agent_command_lines: dict[tuple[str, str], int] = field(default_factory=dict)


# --- scenarios ------------------------------------------------------------------

_SCENARIO_KEYS = (
    "name", "title", "description", "provider", "cwd", "repo", "exclusive", "one_per_chat", "audience",
    "initial", "states",
)
_STATE_KEYS = ("kind", "prompt", "reply", "next", "data", "share", "commands", "agent_commands")
_REPLY_KEYS = ("kinds", "image_templates", "text_templates", "stickers", "max_text_chars")


def _reply_spec(value: Any, line: int, ctx: _Ctx) -> ReplySpec:
    m = _mapping(value, line, "reply")
    _keys(m, _REPLY_KEYS, ("kinds",), "reply")
    kinds = _strs(m, "kinds")
    if not kinds:
        raise _Reject(m.at("kinds"), "reply.kinds 不能为空")
    for kind in kinds:
        if kind not in REPLY_KINDS:
            raise _Reject(m.at("kinds"), f"未知的回复类型：{kind}")
    image_templates = _strs(m, "image_templates")
    for card in image_templates:
        if card not in ctx.cards:
            raise _Reject(m.at("image_templates"), f"卡片模板不存在：templates/cards/{card}.html")
    stickers = _strs(m, "stickers")
    for sticker in stickers:
        if sticker not in ctx.stickers:
            raise _Reject(m.at("stickers"), f"贴纸不存在：{sticker}")
    max_chars = m.get("max_text_chars", 600)
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
        raise _Reject(m.at("max_text_chars"), "max_text_chars 必须是正整数")
    return ReplySpec(
        kinds=kinds,
        image_templates=image_templates,
        text_templates=_strs(m, "text_templates"),
        stickers=stickers,
        max_text_chars=max_chars,
    )


def _state(name: str, value: Any, line: int, directory: Path, ctx: _Ctx) -> State:
    if name in BUILTIN_STATES:
        raise _Reject(line, f"状态名 {name} 是内建状态，不能重新定义")
    m = _mapping(value, line, f"状态 {name}")
    _keys(m, _STATE_KEYS, ("kind",), f"状态 {name}")
    kind = _str(m, "kind")
    reply = _reply_spec(m["reply"], m.at("reply"), ctx) if "reply" in m else None
    data = _strs(m, "data")
    share = _bool(m, "share")
    if kind == "agent":
        _keys(
            m, ("kind", "prompt", "reply", "next", "data", "share", "agent_commands"), ("prompt",), f"状态 {name}"
        )
        prompt_name = _str(m, "prompt")
        prompt_path = directory / prompt_name
        if Path(prompt_name).name != prompt_name or not prompt_path.is_file():
            raise _Reject(m.at("prompt"), f"prompt 文件不存在：{prompt_name}")
        try:
            source = prompt_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise _Reject(m.at("prompt"), f"无法读取 prompt：{exc}") from exc
        try:
            tmpl.check(source, tmpl.PROMPT_VARS)
        except tmpl.TemplateProblem as exc:
            raise _Reject(m.at("prompt"), f"{prompt_name}：{exc}") from exc
        agent_commands = tuple(" ".join(path.split()) for path in _strs(m, "agent_commands"))
        if any(not path for path in agent_commands):
            raise _Reject(m.at("agent_commands"), "agent_commands 里有空的指令路径")
        if len(set(agent_commands)) != len(agent_commands):
            raise _Reject(m.at("agent_commands"), "agent_commands 里有重复的指令路径")
        return AgentState(
            name=name, prompt=Tmpl(source), reply=reply, next=_strs(m, "next"), data=data, share=share,
            agent_commands=agent_commands,
        )
    if kind == "human":
        _keys(m, ("kind", "reply", "data", "share", "commands"), ("commands",), f"状态 {name}")
        commands = _mapping(m["commands"], m.at("commands"), "commands")
        if not commands:
            raise _Reject(m.at("commands"), "human 状态至少要有一个人工指令")
        mapping = {str(k): _str(commands, k) for k in commands}
        return HumanState(name=name, commands=mapping, reply=reply, data=data, share=share)
    if kind == "terminal":
        _keys(m, ("kind", "reply", "data", "share"), (), f"状态 {name}")
        return TerminalState(name=name, reply=reply, data=data, share=share)
    raise _Reject(m.at("kind"), f"未知的状态类型：{kind}（可选：agent | human | terminal）")


def _scenario(path: Path, ctx: _Ctx) -> Scenario:
    m = _mapping(_load_yaml(path), 1, "scenario.yaml")
    _keys(m, _SCENARIO_KEYS, ("name", "title", "provider", "initial", "states"), "scenario")
    name = _str(m, "name")
    if name != path.parent.name:
        raise _Reject(m.at("name"), f"name 必须与目录名一致：{path.parent.name}")
    title = _tmpl(_str(m, "title"), tmpl.TITLE_VARS, m.at("title"))
    if ("cwd" in m) == ("repo" in m):
        raise _Reject(m.line, "cwd 与 repo 必须且只能填一个")
    if "cwd" in m:
        workdir: CwdWorkdir | RepoWorkdir = CwdWorkdir(path=_str(m, "cwd"))
    else:
        repo = _mapping(m["repo"], m.at("repo"), "repo")
        _keys(repo, ("fixed_main", "base"), ("fixed_main", "base"), "repo")
        workdir = RepoWorkdir(fixed_main=_str(repo, "fixed_main"), base=_str(repo, "base"))
    states_map = _mapping(m["states"], m.at("states"), "states")
    states: dict[str, State] = {}
    for state_name in states_map:
        states[str(state_name)] = _state(
            str(state_name), states_map[state_name], states_map.at(state_name), path.parent, ctx
        )
    initial = _str(m, "initial")
    if not isinstance(states.get(initial), AgentState):
        raise _Reject(m.at("initial"), f"initial 必须是一个 agent 状态：{initial}")
    known = set(states) | set(BUILTIN_STATES)
    for state in states.values():
        line = states_map.at(state.name)
        match state:
            case AgentState():
                for target in state.next:
                    if target not in states:
                        raise _Reject(line, f"状态 {state.name} 的 next 引用了不存在的状态：{target}")
                    target_state = states[target]
                    if isinstance(target_state, (HumanState, TerminalState)) and target_state.reply is None:
                        raise _Reject(
                            states_map.at(target),
                            f"状态 {target} 可由 AI 进入，必须声明 reply 规格",
                        )
            case HumanState():
                for command, target in state.commands.items():
                    if target not in known:
                        raise _Reject(line, f"人工指令 {command} 指向不存在的状态：{target}")
            case TerminalState():
                pass
    exclusive = _bool(m, "exclusive")
    one_per_chat = _bool(m, "one_per_chat")
    if exclusive and one_per_chat:
        raise _Reject(m.at("one_per_chat"), "one_per_chat 不能与 exclusive 同用")
    audience = _enum(m, "audience", Audience, "audience") if "audience" in m else Audience.ALL
    for state in states.values():
        if isinstance(state, AgentState) and state.agent_commands:
            ctx.agent_command_lines[(name, state.name)] = states_map.at(state.name)
    return Scenario(
        name=name,
        title=title,
        description=_str(m, "description", required=False),
        provider=_str(m, "provider"),
        workdir=workdir,
        initial=initial,
        states=states,
        exclusive=exclusive,
        one_per_chat=one_per_chat,
        audience=audience,
        source=SourceRef(path=path, line=1),
    )


# --- actions ---------------------------------------------------------------------

_ACTION_FIELDS: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # action: (allowed keys, required keys)
    "start": (("scenario", "text"), ("scenario", "text")),
    "github": (("ref", "scenarios"), ("ref", "scenarios")),
    "send": (("session", "text"), ("text",)),
    "session_show": (("session",), ()),
    "session_switch": (("session",), ()),
    "session_rename": (("session", "name"), ("name",)),
    "session_archive": (("session",), ()),
    "human": (("session", "command"), ("command",)),
    "share": (("session", "to"), ()),
    "favorite": (("quoted",), ("quoted",)),
    "list": (("source", "card", "page", "scenario", "archived"), ("source", "card")),
    "chats": ((), ()),
    "status": ((), ()),
    "help": (("command",), ()),
}


def _arg_ref(
    m: LineMap, key: str, args: Mapping[str, ArgType], expected: ArgType, *, required: bool = False
) -> str | None:
    if key not in m or m[key] is None:
        if required:
            raise _Reject(m.line, f"缺少 {key}")
        return None
    name = _str(m, key)
    if name not in args:
        raise _Reject(m.at(key), f"{key} 引用了未声明的参数：{name}")
    if args[name] is not expected:
        raise _Reject(m.at(key), f"{key} 引用的参数 {name} 必须是 {expected.value} 类型")
    return name


def _action(value: Any, line: int, args: Mapping[str, ArgType], ctx: _Ctx, allowed_vars: frozenset[str]) -> Action:
    m = _mapping(value, line, "do")
    if len(m) != 1:
        raise _Reject(m.line, "do 必须恰好包含一个动作")
    (verb,) = list(m)
    if verb not in _ACTION_FIELDS:
        raise _Reject(m.at(verb), f"未知的动作：{verb}（可选：{' | '.join(_ACTION_FIELDS)}）")
    allowed, required = _ACTION_FIELDS[verb]
    body_value = m[verb]
    body = body_value if isinstance(body_value, LineMap) else _shorthand(verb, body_value, m.at(verb))
    _keys(body, allowed, required, f"动作 {verb}")

    def text(key: str) -> Tmpl:
        return _tmpl(_str(body, key), allowed_vars, body.at(key))

    match verb:
        case "start":
            scenario = _str(body, "scenario")
            _need_scenario(scenario, ctx, body.at("scenario"))
            return StartAction(scenario=scenario, text=text("text"))
        case "github":
            scenarios = _mapping(body["scenarios"], body.at("scenarios"), "scenarios")
            _keys(scenarios, ("issue", "pr"), ("issue", "pr"), "scenarios")
            mapping = {k: _str(scenarios, k) for k in ("issue", "pr")}
            for scenario in mapping.values():
                _need_scenario(scenario, ctx, body.at("scenarios"))
            return GithubAction(ref=text("ref"), scenarios=mapping)
        case "send":
            return SendAction(session=_arg_ref(body, "session", args, ArgType.SESSION), text=text("text"))
        case "session_show":
            return SessionShowAction(session=_arg_ref(body, "session", args, ArgType.SESSION))
        case "session_switch":
            return SessionSwitchAction(session=_arg_ref(body, "session", args, ArgType.SESSION))
        case "session_rename":
            return SessionRenameAction(
                session=_arg_ref(body, "session", args, ArgType.SESSION), name=text("name")
            )
        case "session_archive":
            return SessionArchiveAction(session=_arg_ref(body, "session", args, ArgType.SESSION))
        case "human":
            command = _str(body, "command")
            if command not in ctx.human_commands:
                raise _Reject(body.at("command"), f"没有任何场景声明人工指令：{command}")
            return HumanAction(session=_arg_ref(body, "session", args, ArgType.SESSION), command=command)
        case "share":
            return ShareAction(
                session=_arg_ref(body, "session", args, ArgType.SESSION),
                to=_arg_ref(body, "to", args, ArgType.MENTIONS),
            )
        case "favorite":
            quoted = _arg_ref(body, "quoted", args, ArgType.QUOTED, required=True)
            assert quoted is not None
            return FavoriteAction(quoted=quoted)
        case "list":
            source = _enum(body, "source", ListSource, "列表来源")
            card = _str(body, "card")
            if card not in LIST_CARDS[source]:
                raise _Reject(
                    body.at("card"),
                    f"{source.value} 列表只能用卡片：{' | '.join(sorted(LIST_CARDS[source]))}",
                )
            if card not in ctx.cards:
                raise _Reject(body.at("card"), f"卡片模板不存在：templates/cards/{card}.html")
            scenario = _str(body, "scenario", required=False) or None
            if scenario is not None:
                if source is not ListSource.SESSIONS:
                    raise _Reject(body.at("scenario"), "只有 sessions 列表能按场景过滤")
                _need_scenario(scenario, ctx, body.at("scenario"))
            return ListAction(
                source=source,
                card=card,
                page=_arg_ref(body, "page", args, ArgType.PAGE),
                scenario=scenario,
                archived=_bool(body, "archived"),
            )
        case "chats" | "status":
            action = ChatsAction() if verb == "chats" else StatusAction()
            card = FIXED_CARDS[type(action)]
            if card not in ctx.cards:
                raise _Reject(m.at(verb), f"卡片模板不存在：templates/cards/{card}.html")
            return action
        case "help":
            return HelpAction(command=_arg_ref(body, "command", args, ArgType.WORD))
    raise _Reject(line, f"未知的动作：{verb}")  # unreachable: verb checked above


def _shorthand(verb: str, value: Any, line: int) -> LineMap:
    """``start: requirement`` style is not allowed; every action takes a mapping."""
    if value is None and verb in ("session_show", "session_switch", "session_archive", "help", "share", "chats", "status"):
        empty = LineMap()
        empty.line = line
        return empty
    raise _Reject(line, f"动作 {verb} 的参数必须是映射")


def _need_scenario(name: str, ctx: _Ctx, line: int) -> None:
    if name not in ctx.scenarios:
        raise _Reject(line, f"引用了不存在（或被拒绝）的场景：{name}")


def _started_scenarios(action: Action) -> tuple[str, ...]:
    """Scenarios an action can open a session of."""
    match action:
        case StartAction(scenario=scenario):
            return (scenario,)
        case GithubAction(scenarios=scenarios):
            return tuple(scenarios.values())
        case _:
            return ()


def _admin_starts(action: Action, ctx: _Ctx) -> list[str]:
    return [
        name for name in _started_scenarios(action)
        if name in ctx.scenarios and ctx.scenarios[name].audience is Audience.ADMIN
    ]


# --- commands ---------------------------------------------------------------------

_COMMAND_KEYS = ("name", "type", "aliases", "summary", "usage", "examples", "permission", "args", "do", "say", "subcommands")
_EXAMPLE_NOTE = re.compile(r"^\s*[（(][^）)]*[）)]\s*")


def _args(m: LineMap) -> dict[str, ArgType]:
    if "args" not in m or m["args"] is None:
        return {}
    raw = _mapping(m["args"], m.at("args"), "args")
    out: dict[str, ArgType] = {}
    for name in raw:
        value = raw[name]
        if not isinstance(value, str):
            raise _Reject(raw.at(name), f"参数 {name} 的类型必须是字符串")
        try:
            out[str(name)] = ArgType(value)
        except ValueError as exc:
            choices = " | ".join(t.value for t in ArgType)
            raise _Reject(raw.at(name), f"未知的参数类型：{value}（可选：{choices}）") from exc
    return out


def _say(m: LineMap, action: Action, allowed: frozenset[str]) -> dict[str, Tmpl]:
    if "say" not in m or m["say"] is None:
        return {}
    raw = _mapping(m["say"], m.at("say"), "say")
    results = RESULT_KEYS[type(action)]
    out: dict[str, Tmpl] = {}
    for key in raw:
        if key not in results:
            raise _Reject(
                raw.at(key),
                f"say 的键 {key} 不是动作 {ACTION_NAMES[type(action)]} 的结果（可选：{' | '.join(results)}）",
            )
        out[str(key)] = _tmpl(_str(raw, key), allowed, raw.at(key))
    return out


def _command(m: LineMap, ctx: _Ctx, source: SourceRef, parent: str | None) -> Command:
    _keys(m, _COMMAND_KEYS, ("name", "type", "summary", "usage", "permission", "do"), "指令")
    name = _str(m, "name")
    if not name or any(ch.isspace() or ch in "%％/／" for ch in name):
        raise _Reject(m.at("name"), f"指令名不能为空，也不能含空白、%、/：{name!r}")
    command_type = _enum(m, "type", CommandType, "指令类型")
    permission = _enum(m, "permission", Permission, "权限")
    args = _args(m)
    words = (parent, name) if parent else (name,)
    usage = _str(m, "usage")
    try:
        params = parse_usage(usage, args, words)
    except UsageProblem as exc:
        raise _Reject(m.at("usage"), str(exc)) from exc

    do_vars = tmpl.BASE_VARS | frozenset(args)
    action = _action(m["do"], m.at("do"), args, ctx, do_vars)
    is_ai = isinstance(action, AI_ACTIONS)
    if is_ai != (command_type is CommandType.AI):
        raise _Reject(
            m.at("type"),
            f"type 为 {command_type.value}，但动作 {ACTION_NAMES[type(action)]} 属于"
            f"{'AI' if is_ai else '程序'}指令",
        )
    say = _say(m, action, do_vars | tmpl.RESULT_VARS)
    admin_only = _admin_starts(action, ctx)
    if admin_only and permission is not Permission.ADMIN:
        raise _Reject(
            m.at("permission"), f"场景 {admin_only[0]} 是 audience: admin，启动它的指令必须是 permission: admin"
        )

    subcommands: dict[str, Command] = {}
    if "subcommands" in m and m["subcommands"] is not None:
        if parent is not None:
            raise _Reject(m.at("subcommands"), "子指令不能再有子指令")
        raw = m["subcommands"]
        if not isinstance(raw, list):
            raise _Reject(m.at("subcommands"), "subcommands 必须是列表")
        for item in raw:
            sub_map = _mapping(item, m.at("subcommands"), "子指令")
            sub = _command(sub_map, ctx, SourceRef(path=source.path, line=sub_map.line), parent=name)
            if sub.name in subcommands:
                raise _Reject(sub_map.at("name"), f"子指令重复：{sub.name}")
            subcommands[sub.name] = sub

    aliases = _strs(m, "aliases") if parent is None else ()
    if parent is not None and "aliases" in m:
        raise _Reject(m.at("aliases"), "子指令不能有别名")
    command = Command(
        name=name,
        type=command_type,
        summary=_str(m, "summary"),
        usage=usage,
        params=params,
        examples=_strs(m, "examples"),
        permission=permission,
        do=action,
        say=say,
        aliases=aliases,
        subcommands=subcommands,
        source=source,
    )
    return command


def _check_examples(command: Command, line: int) -> None:
    """Every example must parse against its command's usage (after a leading note)."""
    single = Registry(
        commands={n: command for n in (command.name, *command.aliases)},
        routes=(),
        scenarios={},
        messages={},
        errors=(),
    )
    for expected in (command, *command.subcommands.values()):
        for example in expected.examples:
            text = _EXAMPLE_NOTE.sub("", example, count=1)
            inv = match_command(single, text)
            if inv is None or inv.command is not expected:
                raise _Reject(line, f"示例不是这条指令的用法：{example!r}")
            parsed = parse_args(inv.command, inv)
            if isinstance(parsed, ArgError):
                raise _Reject(line, f"示例按 usage 解析失败（{parsed.message}）：{example!r}")


# --- routes & messages ----------------------------------------------------------------


def _routes(path: Path, ctx: _Ctx) -> tuple[Route, ...]:
    raw = _load_yaml(path)
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise _Reject(1, "routes.yaml 必须是列表")
    routes: list[Route] = []
    vars_ = tmpl.BASE_VARS | tmpl.ROUTE_VARS
    for item in raw:
        m = _mapping(item, 1, "路由")
        _keys(m, ("when", "do", "say"), ("when", "do"), "路由")
        when = _enum(m, "when", RouteWhen, "路由条件")
        action = _action(m["do"], m.at("do"), {}, ctx, vars_)
        admin_only = _admin_starts(action, ctx)
        if admin_only:
            raise _Reject(m.at("do"), f"路由不能启动 audience: admin 的场景：{admin_only[0]}")
        routes.append(Route(when=when, do=action, say=_say(m, action, vars_ | tmpl.RESULT_VARS)))
    return tuple(routes)


def _messages(path: Path) -> dict[str, Tmpl]:
    m = _mapping(_load_yaml(path), 1, "messages.yaml")
    out: dict[str, Tmpl] = {}
    for key in m:
        out[str(key)] = _tmpl(_str(m, key), tmpl.MESSAGE_VARS, m.at(key))
    missing = [k for k in required_keys() if k not in out]
    if missing:
        raise _Reject(1, "messages.yaml 缺少键：" + "、".join(missing))
    return out


# --- entry point ------------------------------------------------------------------------


def load_registry(root: Path) -> Registry:
    root = Path(root)
    errors: list[DslError] = []

    def reject(path: Path, exc: _Reject) -> None:
        errors.append(DslError(path=_rel(root, path), line=exc.line, message=exc.message))

    cards = frozenset(p.stem for p in (root / "cards").glob("*.html"))
    stickers = frozenset(p.stem for p in (root / "stickers").glob("*.png")) | frozenset(
        p.stem for p in (root / "stickers").glob("*.jpg")
    )
    ctx = _Ctx(root=root, cards=cards, stickers=stickers, scenarios={}, human_commands=frozenset())

    scenarios: dict[str, Scenario] = {}
    for path in sorted((root / "scenarios").glob("*/scenario.yaml")):
        try:
            scenario = _scenario(path, ctx)
        except _Reject as exc:
            reject(path, exc)
            continue
        scenarios[scenario.name] = scenario
    ctx.scenarios = scenarios
    ctx.human_commands = frozenset(
        command
        for scenario in scenarios.values()
        for state in scenario.states.values()
        if isinstance(state, HumanState)
        for command in state.commands
    )

    messages: dict[str, Tmpl] = {}
    messages_path = root / "messages.yaml"
    if messages_path.is_file():
        try:
            messages = _messages(messages_path)
        except _Reject as exc:
            reject(messages_path, exc)
    else:
        errors.append(DslError(path="messages.yaml", line=0, message="缺少 messages.yaml"))

    commands: dict[str, Command] = {}
    owners: dict[str, Path] = {}
    rejected_commands: set[str] = set()  # file stems (= command names) rejected on their own
    for path in sorted((root / "commands").glob("*.yaml")):
        try:
            m = _mapping(_load_yaml(path), 1, "指令文件")
            command = _command(m, ctx, SourceRef(path=path, line=1), parent=None)
            if path.stem != command.name:
                raise _Reject(m.at("name"), f"文件名必须是 <指令名>.yaml：{command.name}.yaml")
            _check_examples(command, m.at("examples"))
            for key in (command.name, *command.aliases):
                if key in commands:
                    raise _Reject(
                        m.at("aliases") if key != command.name else m.at("name"),
                        f"指令名或别名 {key} 与 {_rel(root, owners[key])} 冲突",
                    )
        except _Reject as exc:
            reject(path, exc)
            rejected_commands.add(path.stem)
            continue
        for key in (command.name, *command.aliases):
            commands[key] = command
            owners[key] = path

    routes: tuple[Route, ...] = ()
    routes_path = root / "routes.yaml"
    if routes_path.is_file():
        try:
            routes = _routes(routes_path, ctx)
        except _Reject as exc:
            reject(routes_path, exc)

    _check_agent_commands(scenarios, commands, owners, rejected_commands, ctx, reject)
    route_scenarios = {name for route in routes for name in _referenced_scenarios(route.do)}
    if route_scenarios - set(scenarios):
        errors.append(DslError(path="routes.yaml", line=1, message="路由引用的场景已被拒绝"))
        routes = ()
    return Registry(
        commands=commands,
        routes=routes,
        scenarios=scenarios,
        messages=messages,
        errors=tuple(errors),
    )


def _referenced_scenarios(action: Action) -> tuple[str, ...]:
    if isinstance(action, ListAction) and action.scenario is not None:
        return (action.scenario,)
    return _started_scenarios(action)


def _command_scenarios(command: Command) -> set[str]:
    names = set(_referenced_scenarios(command.do))
    for sub in command.subcommands.values():
        names |= _command_scenarios(sub)
    return names


def _agent_commands_problem(
    scenario: Scenario, registry: Registry, rejected_commands: set[str]
) -> str | None:
    for state in scenario.states.values():
        if not isinstance(state, AgentState):
            continue
        for path in state.agent_commands:
            command = registry.command_at(path)
            if command is None:
                if path.split()[0] in rejected_commands:
                    continue  # that file's own error is reported; only this entry is unavailable
                return f"状态 {state.name} 的 agent_commands 引用了不存在的指令：{path}"
            if not isinstance(command.do, AGENT_ACTIONS):
                return f"指令 {path} 的动作 {ACTION_NAMES[type(command.do)]} 不能由 agent 调用"
            if command.permission is Permission.ADMIN and scenario.audience is not Audience.ADMIN:
                return f"指令 {path} 需要管理员权限，只能出现在 audience: admin 场景的 agent_commands 里"
    return None


def _check_agent_commands(
    scenarios: dict[str, Scenario],
    commands: dict[str, Command],
    owners: dict[str, Path],
    rejected_commands: set[str],
    ctx: _Ctx,
    reject: Callable[[Path, "_Reject"], None],
) -> None:
    """Scenarios whose agent_commands name an unknown or forbidden command are rejected,
    then commands that start or list a rejected scenario are rejected (§3.4, §3.5). An
    entry naming a command file rejected on its own is only unavailable: one broken
    command must not take every scenario that lists it down."""
    view = Registry(commands=commands, routes=(), scenarios=scenarios, messages={}, errors=())
    for scenario in list(scenarios.values()):
        problem = _agent_commands_problem(scenario, view, rejected_commands)
        if problem is None:
            continue
        line = next((ln for (sc, _), ln in ctx.agent_command_lines.items() if sc == scenario.name), 1)
        assert scenario.source is not None
        reject(scenario.source.path, _Reject(line, problem))
        del scenarios[scenario.name]
    for key, command in list(commands.items()):
        if key != command.name:
            continue
        missing = sorted(name for name in _command_scenarios(command) if name not in scenarios)
        if not missing:
            continue
        reject(owners[key], _Reject(1, f"引用了被拒绝的场景：{missing[0]}"))
        for alias in (command.name, *command.aliases):
            commands.pop(alias, None)


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)

