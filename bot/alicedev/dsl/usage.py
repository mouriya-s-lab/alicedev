"""Parse a command ``usage`` line into typed parameters (ARCHITECTURE §3.2).

Syntax after the command words (``/name`` and, for a subcommand, its word):

* ``<名称>``   required parameter
* ``[名称]``   optional parameter
* ``%名称`` / ``[%名称]`` / ``<%名称>``  session parameter (``%n`` right after the command words)
* ``[@名称…]``  mentions (taken from @ components, never typed as text)
* ``（名称）``  quoted message (the user must quote a message; never typed)

The marker must agree with the declared ``args`` type: ``%`` ⇔ session,
``@`` ⇔ mentions, ``（）`` ⇔ quoted. A session parameter must be the first
parameter; a ``text`` parameter must be the last one.
"""

from __future__ import annotations

from typing import Mapping

from alicedev.dsl.model import ArgType, UsageParam


class UsageProblem(ValueError):
    pass


_PREFIXES = ("/", "／")
_EVENT_TYPES = (ArgType.MENTIONS, ArgType.QUOTED)


def command_words(usage: str) -> list[str]:
    tokens = usage.split()
    if not tokens or not tokens[0].startswith(_PREFIXES):
        raise UsageProblem("usage 必须以 /指令名 开头")
    return tokens


def parse_usage(
    usage: str, args: Mapping[str, ArgType], words: tuple[str, ...]
) -> tuple[UsageParam, ...]:
    """Parse ``usage``; ``words`` = expected command words (name[, subcommand])."""

    tokens = command_words(usage)
    head = [tokens[0][1:]] + tokens[1 : len(words)]
    if tuple(head) != words:
        expected = "/" + " ".join(words)
        raise UsageProblem(f"usage 必须以 {expected} 开头")
    params: list[UsageParam] = []
    for token in tokens[len(words) :]:
        params.append(_param(token, args))

    names = [p.name for p in params]
    if len(set(names)) != len(names):
        raise UsageProblem("usage 里参数名重复")
    missing = sorted(set(args) - set(names))
    if missing:
        raise UsageProblem("args 里的参数没有出现在 usage 中：" + "、".join(missing))
    for index, param in enumerate(params):
        if param.type is ArgType.SESSION and index != 0:
            raise UsageProblem("会话参数（%）必须是第一个参数")
        if param.type is ArgType.TEXT and any(
            p.type not in _EVENT_TYPES for p in params[index + 1 :]
        ):
            raise UsageProblem("text 参数必须是最后一个参数")
    return tuple(params)


def _param(token: str, args: Mapping[str, ArgType]) -> UsageParam:
    optional: bool
    inner: str
    if token.startswith("[") and token.endswith("]"):
        optional, inner = True, token[1:-1]
    elif token.startswith("<") and token.endswith(">"):
        optional, inner = False, token[1:-1]
    elif token.startswith("（") and token.endswith("）"):
        optional, inner = False, token[1:-1]
        return _typed(inner, args, marker="quoted", optional=optional, token=token)
    elif token.startswith("%"):
        optional, inner = False, token
    else:
        raise UsageProblem(f"无法识别的 usage 片段：{token}（参数用 <> 或 [] 包裹）")

    if inner.startswith("%"):
        return _typed(inner[1:], args, marker="session", optional=optional, token=token)
    if inner.startswith("@"):
        name = inner[1:].rstrip("…").rstrip(".")
        return _typed(name, args, marker="mentions", optional=optional, token=token)
    return _typed(inner, args, marker="plain", optional=optional, token=token)


def _typed(
    name: str, args: Mapping[str, ArgType], *, marker: str, optional: bool, token: str
) -> UsageParam:
    if not name:
        raise UsageProblem(f"usage 片段缺少参数名：{token}")
    if name not in args:
        raise UsageProblem(f"usage 里的参数 {name} 没有在 args 中声明")
    arg_type = args[name]
    expected = {
        "session": ArgType.SESSION,
        "mentions": ArgType.MENTIONS,
        "quoted": ArgType.QUOTED,
    }.get(marker)
    if expected is not None and arg_type is not expected:
        raise UsageProblem(f"参数 {name} 的写法 {token} 要求类型 {expected.value}，实际是 {arg_type.value}")
    if expected is None and arg_type in (ArgType.SESSION, ArgType.MENTIONS, ArgType.QUOTED):
        hint = {"session": "%", "mentions": "@", "quoted": "（）"}[arg_type.value]
        raise UsageProblem(f"参数 {name} 是 {arg_type.value} 类型，usage 里要写成带 {hint} 的形式")
    return UsageParam(name=name, type=arg_type, optional=optional)
